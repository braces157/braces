#!/usr/bin/env python3
"""Deterministic frontend anti-slop, accessibility, and RSC/barrel hygiene checker."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".next",
    "dist",
    "build",
    "out",
    "target",
    ".venv",
    "venv",
    "__pycache__",
    ".turbo",
    ".wt",
    "coverage",
    ".cache",
}

UI_EXTENSIONS = {".tsx", ".jsx", ".css", ".html"}
MARKUP_EXTENSIONS = {".tsx", ".jsx", ".html"}
CLIENT_EXTENSIONS = {".tsx", ".jsx"}
BARREL_FILENAMES = {"index.ts", "index.tsx", "index.js", "index.jsx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

ALWAYS_FLAG_SCRATCH_DIRS = {
    ".taste-skill",
    ".screenshots",
    "screenshot-dumps",
    "playwright-screenshots",
    "tmp-screenshots",
}
CONDITIONAL_SCRATCH_DIRS = {"screenshots"}

DESIGN_DOC_CANDIDATES = (
    "DESIGN.md",
    "design.md",
    "docs/DESIGN.md",
    "docs/design.md",
)

SEVERITY_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}

HEADING_OPEN_RE = re.compile(r"<h([1-6])(?:\s|>|/)", re.IGNORECASE)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
JSX_COMMENT_RE = re.compile(r"\{\s*/\*.*?\*/\s*\}", re.DOTALL)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

BUTTON_OPEN_RE = re.compile(r"<button(?=\s|>)", re.IGNORECASE)
BUTTON_CLOSE_RE = re.compile(r"</button\s*>", re.IGNORECASE)
ACCESSIBLE_LABEL_ATTR_RE = re.compile(
    r"\b(?:aria-label|aria-labelledby|title)\s*=",
    re.IGNORECASE,
)
SR_ONLY_RE = re.compile(
    r"\b(?:sr-only|visually-hidden|visuallyHidden|ScreenReaderOnly)\b"
)
SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.IGNORECASE | re.DOTALL)
ICON_I_BLOCK_RE = re.compile(r"<i\b[^>]*>\s*</i>", re.IGNORECASE | re.DOTALL)
IMG_TAG_RE = re.compile(r"<img\b[^>]*/?>", re.IGNORECASE | re.DOTALL)
COMPONENT_OPEN_RE = re.compile(r"<[A-Z][A-Za-z0-9_.]*(?=\s|/|>)", re.DOTALL)
EMPTY_WRAPPER_TAG_RE = re.compile(r"</?(?:span|div)\b[^>]*>", re.IGNORECASE)

TW_PURPLE_GRADIENT_RE = re.compile(
    r"\bbg-(?:gradient|linear)-to-[a-z]+\b[^\n\"'`]*?"
    r"\bfrom-(?:purple|indigo|violet)-\d+\b[^\n\"'`]*?"
    r"\b(?:via|to)-(?:indigo|purple|violet|fuchsia|pink|blue)-\d+\b",
    re.IGNORECASE,
)
TW_PURPLE_GRADIENT_ALT_RE = re.compile(
    r"\bfrom-(?:purple|indigo|violet)-\d+\b[^\n\"'`]*?"
    r"\b(?:via|to)-(?:indigo|purple|violet|fuchsia|pink|blue)-\d+\b[^\n\"'`]*?"
    r"\bbg-(?:gradient|linear)-to-[a-z]+\b",
    re.IGNORECASE,
)
CSS_GRADIENT_LINE_RE = re.compile(r"(?:linear|radial)-gradient\(([^)]+)\)", re.IGNORECASE)
AI_PURPLE_COLOR_RE = re.compile(
    r"(?:#(?:6366f1|4f46e5|8b5cf6|7c3aed|a855f7|9333ea|c084fc|ec4899|d946ef)\b"
    r"|\b(?:indigo|violet|purple|fuchsia)\b)",
    re.IGNORECASE,
)

DEFAULT_FONT_RE = re.compile(
    r"font-family\s*:\s*['\"]?(?:Inter|Roboto|Arial|Helvetica|system-ui|-apple-system)\b",
    re.IGNORECASE,
)
CUSTOM_FONT_TOKEN_RE = re.compile(
    r"(?:@font-face\b|next/font/|@next/font/|--font-[a-zA-Z0-9_-]+\s*:|\bfontFamily\s*:)"
)

USE_CLIENT_RE = re.compile(r"""^\s*["']use client["']\s*;?\s*(?://.*)?$""")
EXPORT_STAR_RE = re.compile(
    r"""^\s*export\s+\*\s+(?:as\s+\w+\s+)?from\s+["'][^"']+["']"""
)
EXPORT_FROM_RE = re.compile(r"""^\s*export\b[^\n]*\bfrom\s+["'][^"']+["']""")


def configure_stdio() -> None:
    """Force UTF-8 output streams with replacement to prevent Windows cp1252 crashes."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            reconf(encoding="utf-8", errors="replace")


def rel_posix(path: Path, repo: Path) -> str:
    """Return a normalized POSIX relative path string."""
    return path.relative_to(repo).as_posix()


def strip_preserving_newlines(pattern: re.Pattern[str], text: str) -> str:
    """Replace regex matches with equal newline counts so line numbers remain exact."""
    return pattern.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def line_number_at(text: str, offset: int) -> int:
    """Return 1-based line number for a character offset in text."""
    return text.count("\n", 0, offset) + 1


def load_simple_gitignore_patterns(repo: Path) -> list[str]:
    """Parse basic root .gitignore patterns for fallback when git is unavailable."""
    gitignore = repo / ".gitignore"
    if not gitignore.is_file():
        return []
    patterns: list[str] = []
    try:
        for raw in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            patterns.append(line.lstrip("/"))
    except OSError:
        return []
    return patterns


def check_git_ignored(repo: Path, rel_paths: Sequence[str]) -> set[str]:
    """Return the subset of repo-relative POSIX paths ignored by git (or .gitignore)."""
    if not rel_paths:
        return set()

    if (repo / ".git").exists():
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo), "check-ignore", "--stdin"],
                input="\n".join(rel_paths) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=10.0,
                check=False,
            )
            if proc.returncode in (0, 1):
                ignored = set()
                for line in proc.stdout.splitlines():
                    cleaned = line.strip().strip('"').replace("\\", "/")
                    if cleaned:
                        ignored.add(cleaned.rstrip("/"))
                return ignored
        except (OSError, subprocess.TimeoutExpired):
            pass

    patterns = load_simple_gitignore_patterns(repo)
    if not patterns:
        return set()
    ignored_fallback: set[str] = set()
    for rel in rel_paths:
        name = Path(rel).name
        for pat in patterns:
            pat_clean = pat.rstrip("/")
            if (
                fnmatch.fnmatch(rel, pat_clean)
                or fnmatch.fnmatch(name, pat_clean)
                or (pat.endswith("/") and rel.split("/", 1)[0] == pat_clean)
            ):
                ignored_fallback.add(rel)
                break
    return ignored_fallback


def detect_design_grounding(repo: Path, source_files: Sequence[Path]) -> dict[str, bool]:
    """Check whether repo defines DESIGN.md or custom font tokens."""
    has_design_md = any((repo / rel).is_file() for rel in DESIGN_DOC_CANDIDATES)
    has_custom_font = False

    config_candidates = [
        repo / name
        for name in (
            "tailwind.config.js",
            "tailwind.config.ts",
            "tailwind.config.mjs",
            "tailwind.config.cjs",
        )
        if (repo / name).is_file()
    ]
    for path in list(source_files) + config_candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if CUSTOM_FONT_TOKEN_RE.search(text):
            has_custom_font = True
            break

    return {
        "has_design_md": has_design_md,
        "has_custom_font_token": has_custom_font,
    }


def make_finding(severity: str, code: str, file_rel: str, line: int, message: str) -> dict[str, Any]:
    """Build a normalized finding dictionary."""
    return {
        "severity": severity,
        "code": code,
        "file": file_rel,
        "line": line,
        "message": message,
    }


def scan_scratch_screenshots(repo: Path) -> list[dict[str, Any]]:
    """Detect scratch screenshot folders and unignored root .png files inside repo."""
    findings: list[dict[str, Any]] = []
    candidate_dirs: list[tuple[str, Path]] = []
    root_pngs: list[tuple[str, Path]] = []

    for entry in sorted(repo.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_file() and entry.suffix.lower() == ".png":
            root_pngs.append((entry.name, entry))

    for root, dirs, files in os.walk(repo):
        root_path = Path(root)
        kept_dirs: list[str] = []
        for dirname in sorted(dirs):
            low = dirname.lower()
            if low in SKIP_DIRS or dirname in SKIP_DIRS:
                continue
            full = root_path / dirname
            rel = rel_posix(full, repo)
            if low in ALWAYS_FLAG_SCRATCH_DIRS:
                candidate_dirs.append((rel, full))
                continue
            if low in CONDITIONAL_SCRATCH_DIRS:
                try:
                    has_images = any(
                        (full / f).suffix.lower() in IMAGE_EXTENSIONS
                        for f in os.listdir(full)
                        if (full / f).is_file()
                    )
                except OSError:
                    has_images = False
                if has_images or root_path == repo:
                    candidate_dirs.append((rel, full))
                    continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs

    ignore_probe = [rel for rel, _ in candidate_dirs] + [rel for rel, _ in root_pngs]
    ignored = check_git_ignored(repo, ignore_probe)

    for rel, _ in candidate_dirs:
        if rel in ignored:
            continue
        findings.append(
            make_finding(
                "HIGH",
                "REPO_SCRATCH_SCREENSHOTS",
                rel,
                1,
                f"Scratch screenshot folder '{rel}/' found inside repo; store proof captures in OS temp instead.",
            )
        )

    for rel, _ in root_pngs:
        if rel in ignored:
            continue
        findings.append(
            make_finding(
                "HIGH",
                "REPO_SCRATCH_SCREENSHOTS",
                rel,
                1,
                f"Unignored root PNG '{rel}' found inside repo; move verification screenshots to OS temp.",
            )
        )

    return findings


def check_headings(file_rel: str, text: str) -> list[dict[str, Any]]:
    """Detect skipped heading hierarchy levels (e.g. h1 directly to h3)."""
    cleaned = strip_preserving_newlines(HTML_COMMENT_RE, text)
    cleaned = strip_preserving_newlines(JSX_COMMENT_RE, cleaned)

    findings: list[dict[str, Any]] = []
    prev_level: int | None = None
    for match in HEADING_OPEN_RE.finditer(cleaned):
        level = int(match.group(1))
        if prev_level is not None and level > prev_level + 1:
            line = line_number_at(cleaned, match.start())
            findings.append(
                make_finding(
                    "MED",
                    "HEADING_SKIP",
                    file_rel,
                    line,
                    f"Skipped heading level: <h{prev_level}> followed by <h{level}> (missing <h{prev_level + 1}>).",
                )
            )
        prev_level = level
    return findings


def find_tag_close(text: str, start_idx: int) -> int:
    """Find the closing '>' of an HTML/JSX opening tag, respecting quotes and { ... }."""
    n = len(text)
    i = start_idx
    quote: str | None = None
    brace_depth = 0
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = None
        else:
            if ch in ('"', "'", "`"):
                quote = ch
            elif ch == "{":
                brace_depth += 1
            elif ch == "}" and brace_depth > 0:
                brace_depth -= 1
            elif ch == ">" and brace_depth == 0:
                return i
        i += 1
    return -1


def strip_self_closing_components(body: str) -> tuple[str, bool]:
    """Remove self-closing JSX component tags (<Icon ... />) handling arrow functions."""
    out: list[str] = []
    pos = 0
    found = False
    for match in COMPONENT_OPEN_RE.finditer(body):
        if match.start() < pos:
            continue
        end_gt = find_tag_close(body, match.end())
        if end_gt == -1:
            continue
        inner = body[match.end() : end_gt].rstrip()
        if inner.endswith("/"):
            out.append(body[pos : match.start()])
            pos = end_gt + 1
            found = True
    out.append(body[pos:])
    return "".join(out), found


def check_icon_buttons(file_rel: str, text: str) -> list[dict[str, Any]]:
    """Detect icon-only <button> elements lacking aria-label, aria-labelledby, title, or sr-only text."""
    cleaned = strip_preserving_newlines(HTML_COMMENT_RE, text)
    cleaned = strip_preserving_newlines(JSX_COMMENT_RE, cleaned)

    findings: list[dict[str, Any]] = []
    pos = 0
    for match in BUTTON_OPEN_RE.finditer(cleaned):
        if match.start() < pos:
            continue
        open_end = find_tag_close(cleaned, match.end())
        if open_end == -1:
            continue
        attrs = cleaned[match.end() : open_end]
        if attrs.rstrip().endswith("/"):
            pos = open_end + 1
            continue

        close_match = BUTTON_CLOSE_RE.search(cleaned, open_end + 1)
        if close_match is None:
            continue

        body = cleaned[open_end + 1 : close_match.start()]
        pos = close_match.end()

        if BUTTON_OPEN_RE.search(body):
            continue
        if ACCESSIBLE_LABEL_ATTR_RE.search(attrs):
            continue
        if SR_ONLY_RE.search(body):
            continue

        stripped_body, has_component_icon = strip_self_closing_components(body)
        has_icon = bool(
            has_component_icon
            or SVG_BLOCK_RE.search(stripped_body)
            or ICON_I_BLOCK_RE.search(stripped_body)
            or IMG_TAG_RE.search(stripped_body)
        )
        if not has_icon:
            continue

        remainder = SVG_BLOCK_RE.sub("", stripped_body)
        remainder = ICON_I_BLOCK_RE.sub("", remainder)
        remainder = IMG_TAG_RE.sub("", remainder)
        remainder = EMPTY_WRAPPER_TAG_RE.sub("", remainder)
        remainder = remainder.strip()

        if not remainder:
            line = line_number_at(cleaned, match.start())
            findings.append(
                make_finding(
                    "HIGH",
                    "ICON_BUTTON_NO_LABEL",
                    file_rel,
                    line,
                    "Icon-only <button> lacks aria-label, aria-labelledby, title, or sr-only text.",
                )
            )
    return findings


def check_slop_styles(
    file_rel: str,
    text: str,
    *,
    has_design_grounding: bool,
) -> list[dict[str, Any]]:
    """Detect generic purple-indigo gradient spam and unconfigured default font stacks."""
    findings: list[dict[str, Any]] = []
    cleaned = strip_preserving_newlines(CSS_COMMENT_RE, text)

    for idx, line in enumerate(cleaned.splitlines(), start=1):
        tw_hit = TW_PURPLE_GRADIENT_RE.search(line) or TW_PURPLE_GRADIENT_ALT_RE.search(line)
        css_hit = False
        for grad_match in CSS_GRADIENT_LINE_RE.finditer(line):
            stops = AI_PURPLE_COLOR_RE.findall(grad_match.group(1))
            if len(stops) >= 2:
                css_hit = True
                break
        if tw_hit or css_hit:
            severity = "LOW" if has_design_grounding else "MED"
            findings.append(
                make_finding(
                    severity,
                    "SLOP_PURPLE_GRADIENT",
                    file_rel,
                    idx,
                    "Generic purple/indigo gradient detected; ground brand accents in explicit DESIGN.md tokens.",
                )
            )

        if not has_design_grounding and DEFAULT_FONT_RE.search(line) and "var(--" not in line:
            findings.append(
                make_finding(
                    "LOW",
                    "UNCONFIGURED_DEFAULT_FONT",
                    file_rel,
                    idx,
                    "Default font stack used with no DESIGN.md or custom font token (--font-*/next/font/@font-face).",
                )
            )

    return findings


def check_client_monolith(
    file_rel: str,
    text: str,
    *,
    max_client_lines: int,
) -> list[dict[str, Any]]:
    """Detect large (>max_client_lines) React/Next.js files marked with 'use client'."""
    lines = text.splitlines()
    if len(lines) <= max_client_lines:
        return []

    non_empty_seen = 0
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue
        non_empty_seen += 1
        if USE_CLIENT_RE.match(line):
            return [
                make_finding(
                    "HIGH",
                    "CLIENT_MONOLITH",
                    file_rel,
                    idx,
                    (
                        f"'use client' monolith is {len(lines)} lines (threshold >{max_client_lines}L); "
                        "push 'use client' down to narrow interactive leaf islands."
                    ),
                )
            ]
        if non_empty_seen >= 5:
            break
    return []


def check_barrel_reexports(
    file_rel: str,
    text: str,
    *,
    min_star_exports: int,
) -> list[dict[str, Any]]:
    """Detect barrel index files using wildcard 'export * from' re-exports."""
    star_lines: list[int] = []
    total_reexports = 0
    for idx, line in enumerate(text.splitlines(), start=1):
        if EXPORT_STAR_RE.match(line):
            star_lines.append(idx)
            total_reexports += 1
        elif EXPORT_FROM_RE.match(line):
            total_reexports += 1

    if not star_lines:
        return []
    if len(star_lines) >= min_star_exports or total_reexports >= 4:
        return [
            make_finding(
                "MED",
                "LARGE_BARREL_REEXPORT",
                file_rel,
                star_lines[0],
                (
                    f"Barrel file '{file_rel}' has {len(star_lines)} wildcard 'export *' "
                    f"statement(s) ({total_reexports} total re-exports); prefer direct leaf imports."
                ),
            )
        ]
    return []


def collect_target_files(repo: Path) -> tuple[list[Path], list[Path]]:
    """Walk repo skipping build/vendor dirs and collect UI source files and barrel index files."""
    ui_files: list[Path] = []
    barrel_files: list[Path] = []

    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in SKIP_DIRS
            and d.lower() not in SKIP_DIRS
            and d.lower() not in ALWAYS_FLAG_SCRATCH_DIRS
        )
        root_path = Path(root)
        for filename in sorted(files):
            path = root_path / filename
            suffix = path.suffix.lower()
            if suffix in UI_EXTENSIONS:
                ui_files.append(path)
            if filename.lower() in BARREL_FILENAMES:
                barrel_files.append(path)

    return ui_files, barrel_files


def audit_repo(
    repo: Path,
    *,
    max_client_lines: int = 400,
    min_barrel_exports: int = 2,
) -> tuple[dict[str, Any], int]:
    """Run all deterministic UI anti-slop and hygiene checks on a repository."""
    if not repo.exists():
        raise FileNotFoundError(f"Repository path does not exist: {repo}")
    if not repo.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo}")

    resolved_repo = repo.resolve()
    ui_files, barrel_files = collect_target_files(resolved_repo)
    design_grounding = detect_design_grounding(resolved_repo, ui_files)
    has_grounding = (
        design_grounding["has_design_md"] or design_grounding["has_custom_font_token"]
    )

    findings: list[dict[str, Any]] = []
    findings.extend(scan_scratch_screenshots(resolved_repo))

    for path in ui_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_rel = rel_posix(path, resolved_repo)
        suffix = path.suffix.lower()

        if suffix in MARKUP_EXTENSIONS:
            findings.extend(check_headings(file_rel, text))
            findings.extend(check_icon_buttons(file_rel, text))

        findings.extend(
            check_slop_styles(file_rel, text, has_design_grounding=has_grounding)
        )

        if suffix in CLIENT_EXTENSIONS:
            findings.extend(
                check_client_monolith(
                    file_rel, text, max_client_lines=max_client_lines
                )
            )

    for path in barrel_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_rel = rel_posix(path, resolved_repo)
        findings.extend(
            check_barrel_reexports(
                file_rel, text, min_star_exports=min_barrel_exports
            )
        )

    findings.sort(
        key=lambda f: (
            SEVERITY_ORDER.get(f["severity"], 9),
            f["file"],
            f["line"],
            f["code"],
        )
    )

    counts = {"HIGH": 0, "MED": 0, "LOW": 0}
    for item in findings:
        if item["severity"] in counts:
            counts[item["severity"]] += 1

    scanned_unique = {str(p) for p in ui_files} | {str(p) for p in barrel_files}
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "clean" if not findings else "findings",
        "repo": str(resolved_repo),
        "design_grounding": design_grounding,
        "files_scanned": len(scanned_unique),
        "summary": {
            "total": len(findings),
            "HIGH": counts["HIGH"],
            "MED": counts["MED"],
            "LOW": counts["LOW"],
        },
        "findings": findings,
    }
    return payload, (0 if not findings else 1)


def render_text(payload: dict[str, Any]) -> str:
    """Render concise human-readable findings report with file:line references."""
    summary = payload["summary"]
    dg = payload["design_grounding"]
    lines = [
        f"UI Anti-Slop Check: {payload['status'].upper()} ({payload['repo']})",
        (
            f"  Files scanned: {payload['files_scanned']} | "
            f"DESIGN.md: {'yes' if dg['has_design_md'] else 'no'} | "
            f"Custom font token: {'yes' if dg['has_custom_font_token'] else 'no'}"
        ),
        (
            f"  Findings: {summary['total']} "
            f"(HIGH={summary['HIGH']}, MED={summary['MED']}, LOW={summary['LOW']})"
        ),
    ]
    if payload["findings"]:
        lines.append("")
        for item in payload["findings"]:
            lines.append(
                f"  [{item['severity']}] {item['file']}:{item['line']} "
                f"({item['code']}) {item['message']}"
            )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Deterministically scan a repository for UI anti-slop tells, "
            "accessibility gaps, RSC monoliths, barrel bloat, and scratch screenshot dumps."
        )
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Repository root directory to scan (default: '.').",
    )
    parser.add_argument(
        "--max-client-lines",
        type=int,
        default=400,
        help="Line threshold for flagging 'use client' monolith files (default: 400).",
    )
    parser.add_argument(
        "--min-barrel-exports",
        type=int,
        default=2,
        help="Minimum wildcard 'export *' statements in an index file to flag (default: 2).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit structured JSON output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Exit codes: 0=clean, 1=findings, 2=error."""
    configure_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return 0 if code == 0 else 2

    if args.max_client_lines < 1 or args.min_barrel_exports < 1:
        print("error: thresholds must be positive integers.", file=sys.stderr)
        return 2

    try:
        payload, exit_code = audit_repo(
            args.repo,
            max_client_lines=args.max_client_lines,
            min_barrel_exports=args.min_barrel_exports,
        )
    except (ValueError, FileNotFoundError, OSError) as exc:
        if args.as_json:
            print(
                json.dumps(
                    {"schema_version": 1, "status": "error", "error": str(exc)},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_text(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
