#!/usr/bin/env python3
"""Scan a repo for monolith hotspots, churn, and polyglot smells (stdlib only).

Separates Rust inline #[cfg(test)] blocks from production LOC and optionally
emits a self-contained HTML + Mermaid report strictly into OS temp.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Sequence

SCHEMA_VERSION = 1

LANGUAGE_BY_EXT: dict[str, str] = {
    ".rs": "rust", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".py": "python", ".pyi": "python", ".java": "java", ".kt": "kotlin",
    ".go": "go", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".scala": "scala", ".sh": "shell", ".ps1": "powershell",
}

IGNORED_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", "node_modules", "target", "dist", "build", "out",
    "coverage", ".next", ".nuxt", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".gradle", ".idea", ".vscode", ".audit",
    ".wt", "vendor",
})

SKIPPED_SUFFIXES: tuple[str, ...] = (
    ".min.js", ".min.mjs", ".bundle.js", ".d.ts",
    ".generated.ts", ".generated.js", ".pb.go", "_pb2.py",
)

TEST_DIR_NAMES: frozenset[str] = frozenset({
    "test", "tests", "__tests__", "spec", "specs", "e2e", "fixtures",
})

HIGH_RISK_SMELLS: frozenset[str] = frozenset({
    "rust_lock_unwrap", "rust_unsafe", "ts_ignore",
    "py_bare_except", "py_broad_except", "java_broad_catch",
})
ADVISORY_SMELLS: frozenset[str] = frozenset({"ts_any"})

CFG_TEST_RE = re.compile(
    r"#\s*\[\s*cfg\s*\(\s*(?:test\s*\)|all\s*\(\s*test\b[^\]]*\)\s*\))\s*\]"
)
RUST_LOCK_UNWRAP_RE = re.compile(r"\.(?:lock|read|write)\s*\(\s*\)\s*\.unwrap\s*\(")
RUST_UNSAFE_RE = re.compile(r"\bunsafe\s*\{")
TS_ANY_RE = re.compile(r"(?::\s*any\b|\bas\s+any\b)")
TS_IGNORE_RE = re.compile(r"@ts-ignore\b")
PY_BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:")
PY_BROAD_EXCEPT_RE = re.compile(
    r"^\s*except\s+(?:\(\s*[^)]*\bException\b[^)]*\)|Exception\b)"
)
JAVA_BROAD_CATCH_RE = re.compile(
    r"\bcatch\s*\(\s*(?:final\s+)?(?:java\.lang\.)?Exception\b"
)


class CmdResult(NamedTuple):
    """Result from executing an external subprocess command."""

    returncode: int
    stdout: str
    stderr: str


def configure_stdio() -> None:
    """Force UTF-8 stdout/stderr with replacement to prevent Windows cp1252 crashes."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            reconf(encoding="utf-8", errors="replace")


def run_cmd(
    argv: Sequence[str],
    cwd: Path | None = None,
    stdin_text: str | None = None,
) -> CmdResult:
    """Execute a command via argv list without shell expansion; OSError -> 127."""
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd is not None else None,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        return CmdResult(proc.returncode, proc.stdout, proc.stderr)
    except OSError as exc:
        return CmdResult(127, "", str(exc))


def _norm_path_key(path: Path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _is_subpath(child: Path, parent: Path) -> bool:
    child_key, parent_key = _norm_path_key(child), _norm_path_key(parent)
    if child_key == parent_key:
        return True
    if not parent_key.endswith(os.sep):
        parent_key += os.sep
    return child_key.startswith(parent_key)


def find_repo_root(start: Path) -> Path | None:
    """Resolve the enclosing git repository root from start path, or None if non-git."""
    target = start if start.is_dir() else start.parent
    res = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=target)
    root = res.stdout.strip() if res.returncode == 0 else ""
    return Path(root) if root else None


def is_supported_source_file(rel_posix: str) -> str | None:
    """Return canonical language if rel_posix is a scannable source file, else None."""
    lower = rel_posix.lower()
    if any(lower.endswith(suffix) for suffix in SKIPPED_SUFFIXES):
        return None
    return LANGUAGE_BY_EXT.get(Path(lower).suffix)


def is_standalone_test_file(rel_posix: str) -> bool:
    """Classify dedicated test files using repo-relative POSIX path only."""
    parts = [p.lower() for p in rel_posix.split("/") if p]
    if not parts:
        return False
    dirs, filename = parts[:-1], parts[-1]
    if any(d in TEST_DIR_NAMES for d in dirs):
        return True
    stem = Path(filename).stem
    if (
        filename.startswith("test_")
        or stem.endswith(("_test", "_tests"))
        or ".test." in filename
        or ".spec." in filename
    ):
        return True
    if filename.endswith((".java", ".kt")):
        raw_stem = Path(rel_posix.split("/")[-1]).stem
        if raw_stem.endswith(("Test", "Tests", "IT")):
            return True
    return False


def discover_source_files(
    scan_root: Path, is_git_repo: bool
) -> list[tuple[str, Path, str]]:
    """Return (rel_posix, abs_path, language) via git ls-files with os.walk fallback."""
    candidates: list[str] = []
    if is_git_repo:
        res = run_cmd(
            [
                "git", "-c", "core.quotepath=false",
                "ls-files", "-z", "--cached", "--others", "--exclude-standard",
            ],
            cwd=scan_root,
        )
        if res.returncode == 0 and res.stdout:
            candidates = [p for p in res.stdout.split("\0") if p]

    if not candidates:
        for dirpath, dirnames, filenames in os.walk(scan_root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".git")
            )
            base = Path(dirpath)
            for fname in sorted(filenames):
                try:
                    candidates.append((base / fname).relative_to(scan_root).as_posix())
                except ValueError:
                    continue

    results: list[tuple[str, Path, str]] = []
    seen: set[str] = set()
    for rel in candidates:
        rel_posix = rel.replace("\\", "/").lstrip("./")
        if not rel_posix or rel_posix in seen:
            continue
        if any(part in IGNORED_DIRS for part in rel_posix.split("/")[:-1]):
            continue
        lang = is_supported_source_file(rel_posix)
        if lang is None:
            continue
        full_path = scan_root / rel_posix
        if not full_path.is_file():
            continue
        seen.add(rel_posix)
        results.append((rel_posix, full_path, lang))
    results.sort(key=lambda item: item[0])
    return results


def collect_git_churn(
    scan_root: Path, repo_root: Path | None, churn_days: int
) -> dict[str, int]:
    """Count recent git commits touching each file relative to scan_root."""
    if repo_root is None:
        return {}
    argv = ["git", "-c", "core.quotepath=false", "log", "--name-only", "--pretty=format:"]
    if churn_days > 0:
        argv.append(f"--since={churn_days}.days")
    argv.extend(["--", "."])
    res = run_cmd(argv, cwd=scan_root)
    if res.returncode != 0:
        return {}

    try:
        rel_sub = scan_root.resolve().relative_to(repo_root.resolve())
        rel_prefix = "" if str(rel_sub) == "." else rel_sub.as_posix().rstrip("/") + "/"
    except ValueError:
        rel_prefix = ""

    counts: Counter[str] = Counter()
    for raw_line in res.stdout.splitlines():
        entry = raw_line.strip().replace("\\", "/")
        if not entry:
            continue
        if rel_prefix:
            if not entry.startswith(rel_prefix):
                continue
            entry = entry[len(rel_prefix):]
        if entry:
            counts[entry] += 1
    return dict(counts)


def _strip_rust_line_for_structure(
    line: str,
    block_comment_depth: int,
    in_string: bool,
    raw_string_hashes: int | None,
) -> tuple[str, int, bool, int | None]:
    """Strip Rust comments, strings, raw strings, and char literals from one line."""
    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        if block_comment_depth > 0:
            if line[i : i + 2] == "/*":
                block_comment_depth += 1
                i += 2
            elif line[i : i + 2] == "*/":
                block_comment_depth -= 1
                i += 2
                out.append(" ")
            else:
                i += 1
            continue
        if raw_string_hashes is not None:
            end = i + 1 + raw_string_hashes
            if line[i] == '"' and end <= n and line[i + 1 : end] == "#" * raw_string_hashes:
                raw_string_hashes = None
                i = end
                out.append(" ")
            else:
                i += 1
            continue
        if in_string:
            if line[i] == "\\":
                i += 2
            elif line[i] == '"':
                in_string = False
                i += 1
                out.append(" ")
            else:
                i += 1
            continue

        ch = line[i]
        if ch == "/" and i + 1 < n:
            if line[i + 1] == "/":
                break
            if line[i + 1] == "*":
                block_comment_depth = 1
                i += 2
                continue
        if ch == "r":
            is_raw = i == 0 or not (line[i - 1].isalnum() or line[i - 1] == "_")
            if not is_raw and line[i - 1] in ("b", "c"):
                is_raw = i == 1 or not (line[i - 2].isalnum() or line[i - 2] == "_")
            if is_raw:
                j = i + 1
                while j < n and line[j] == "#":
                    j += 1
                if j < n and line[j] == '"':
                    raw_string_hashes = j - (i + 1)
                    i = j + 1
                    out.append(" ")
                    continue
        if ch == '"':
            in_string = True
            i += 1
            out.append(" ")
            continue
        if ch == "'":
            if i + 1 < n and line[i + 1] == "\\":
                j = min(i + 3, n)
                while j < n and line[j] != "'":
                    j += 1
                if j < n and line[j] == "'":
                    i = j + 1
                    out.append(" ")
                    continue
            elif i + 2 < n and line[i + 1] not in ("'", "\\") and line[i + 2] == "'":
                i += 3
                out.append(" ")
                continue
        out.append(ch)
        i += 1
    return "".join(out), block_comment_depth, in_string, raw_string_hashes


def split_rust_prod_test_lines(
    lines: Sequence[str],
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Split Rust lines into (prod_lines, test_lines) handling #[cfg(test)] items."""
    prod_lines: list[tuple[int, str]] = []
    test_lines: list[tuple[int, str]] = []
    block_depth, in_string, raw_hashes = 0, False, None
    cfg_state, brace_depth, attr_depth = "normal", 0, 0

    for idx, raw_line in enumerate(lines, start=1):
        skeleton, block_depth, in_string, raw_hashes = _strip_rust_line_for_structure(
            raw_line, block_depth, in_string, raw_hashes
        )
        is_test_line = cfg_state != "normal"
        segment = skeleton
        if cfg_state == "normal":
            match = CFG_TEST_RE.search(skeleton)
            if match is not None:
                is_test_line, cfg_state, attr_depth = True, "pending_item", 0
                segment = skeleton[match.end():]

        if cfg_state != "normal":
            for ch in segment:
                if cfg_state == "pending_item":
                    if ch == "[":
                        attr_depth += 1
                    elif ch == "]" and attr_depth > 0:
                        attr_depth -= 1
                    elif attr_depth == 0:
                        if ch == "{":
                            cfg_state, brace_depth = "in_block", 1
                        elif ch == ";":
                            cfg_state = "normal"
                            break
                elif cfg_state == "in_block":
                    if ch == "{":
                        brace_depth += 1
                    elif ch == "}":
                        brace_depth -= 1
                        if brace_depth <= 0:
                            cfg_state, brace_depth = "normal", 0
                            break

        (test_lines if is_test_line else prod_lines).append((idx, raw_line))
    return prod_lines, test_lines


def _strip_c_style_line_comment(line: str) -> str:
    """Remove trailing // comment outside single/double quotes."""
    in_single = in_double = False
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and (in_single or in_double):
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "/" and not in_single and not in_double and line[i : i + 2] == "//":
            return line[:i]
        i += 1
    return line


def _iter_c_style_code_lines(
    prod_lines: Sequence[tuple[int, str]],
) -> Iterator[tuple[str, str]]:
    """Yield (raw_line, code_without_comments) for C-style comment languages."""
    in_block = False
    for _, line in prod_lines:
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
                stripped = stripped.split("*/", 1)[1]
            else:
                yield line, ""
                continue
        if stripped.startswith("/*"):
            if "*/" in stripped:
                stripped = stripped.split("*/", 1)[1]
            else:
                in_block = True
                yield line, ""
                continue
        if stripped.startswith(("*", "//")):
            yield line, ""
            continue
        yield line, _strip_c_style_line_comment(stripped)


def detect_smells(language: str, prod_lines: Sequence[tuple[int, str]]) -> dict[str, int]:
    """Count language-specific maintainability and safety smells on prod lines."""
    counts: Counter[str] = Counter()
    if language == "rust":
        b_depth, in_str, raw_h = 0, False, None
        for _, line in prod_lines:
            skel, b_depth, in_str, raw_h = _strip_rust_line_for_structure(
                line, b_depth, in_str, raw_h
            )
            if hits := len(RUST_LOCK_UNWRAP_RE.findall(skel)):
                counts["rust_lock_unwrap"] += hits
            if hits := len(RUST_UNSAFE_RE.findall(skel)):
                counts["rust_unsafe"] += hits
    elif language in ("typescript", "javascript"):
        for raw_line, code in _iter_c_style_code_lines(prod_lines):
            if hits := len(TS_IGNORE_RE.findall(raw_line)):
                counts["ts_ignore"] += hits
            if language == "typescript" and code:
                if hits := len(TS_ANY_RE.findall(code)):
                    counts["ts_any"] += hits
    elif language == "python":
        for _, line in prod_lines:
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if PY_BARE_EXCEPT_RE.match(line):
                counts["py_bare_except"] += 1
            elif PY_BROAD_EXCEPT_RE.match(line):
                counts["py_broad_except"] += 1
    elif language == "java":
        for _, code in _iter_c_style_code_lines(prod_lines):
            if code and (hits := len(JAVA_BROAD_CATCH_RE.findall(code))):
                counts["java_broad_catch"] += hits
    return dict(sorted(counts.items()))


def analyze_file(
    rel_posix: str,
    full_path: Path,
    language: str,
    churn_commits: int,
    soft_limit: int,
    hard_limit: int,
) -> dict[str, Any]:
    """Analyze one source file for prod/test LOC, status, churn, and smells."""
    try:
        raw_bytes = full_path.read_bytes()
    except OSError:
        raw_bytes = b""
    lines = (
        []
        if b"\x00" in raw_bytes[:8192]
        else raw_bytes.decode("utf-8", errors="replace").splitlines()
    )
    total_loc = len(lines)
    standalone_test = is_standalone_test_file(rel_posix)

    if standalone_test:
        prod_loc, test_loc, smells = 0, total_loc, {}
    elif language == "rust":
        prod_lines, test_lines = split_rust_prod_test_lines(lines)
        prod_loc, test_loc = len(prod_lines), len(test_lines)
        smells = detect_smells(language, prod_lines)
    else:
        prod_loc, test_loc = total_loc, 0
        smells = detect_smells(language, list(enumerate(lines, start=1)))

    status = "hard" if prod_loc > hard_limit else ("soft" if prod_loc > soft_limit else "ok")
    high_risk = sum(c for k, c in smells.items() if k in HIGH_RISK_SMELLS)
    advisory = sum(c for k, c in smells.items() if k in ADVISORY_SMELLS)
    risk_score = (
        prod_loc
        + churn_commits * 25
        + high_risk * 80
        + advisory * 20
        + (500 if status == "hard" else 150 if status == "soft" else 0)
    )
    return {
        "path": rel_posix, "language": language, "is_test_file": standalone_test,
        "total_loc": total_loc, "prod_loc": prod_loc, "test_loc": test_loc,
        "status": status, "churn_commits": churn_commits, "smells": smells,
        "high_risk_smells": high_risk, "total_smells": sum(smells.values()),
        "risk_score": risk_score,
    }


def _severity_rank(item: dict[str, Any]) -> int:
    if item["status"] == "hard":
        return 3
    if item["high_risk_smells"] > 0:
        return 2
    return 1 if item["status"] == "soft" else 0


def build_mermaid_summary(hotspots: Sequence[dict[str, Any]], repo_name: str) -> str:
    """Build a Mermaid flowchart summarizing flagged hotspots and extraction seams."""
    safe_repo = re.sub(r'["<>`]', "", repo_name) or "repo"
    lines = ["flowchart TD", f'  ROOT["{safe_repo} Hotspot Survey"]']
    flagged = [
        h for h in hotspots if h["status"] in ("hard", "soft") or h["total_smells"] > 0
    ][:10]
    if not flagged:
        lines.extend(['  CLEAN["No monoliths or smells above thresholds"]', "  ROOT --> CLEAN"])
        return "\n".join(lines)
    for idx, item in enumerate(flagged, start=1):
        safe_path = re.sub(r'["<>`]', "", item["path"])
        smell_note = f" | smells:{item['total_smells']}" if item["total_smells"] else ""
        label = (
            f"{safe_path} ({item['prod_loc']}L prod / {item['test_loc']}L test"
            f" | churn:{item['churn_commits']}{smell_note} | {item['status'].upper()})"
        )
        lines.extend([f'  H{idx}["{label}"]', f"  ROOT --> H{idx}"])
        if item["status"] == "hard" or item["high_risk_smells"] > 0:
            lines.extend([
                f'  S{idx}["Pin behavior -> Subtract -> Extract deep module"]',
                f"  H{idx} -.-> S{idx}",
            ])
    return "\n".join(lines)


def write_html_report(report: dict[str, Any], scan_root: Path) -> Path:
    """Write a self-contained HTML + Mermaid report strictly into OS temp."""
    temp_base = Path(tempfile.gettempdir()) / "arch-refactor-reports"
    temp_base.mkdir(parents=True, exist_ok=True)
    repo_name = scan_root.name or "repository"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name).strip("-") or "repo"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = temp_base / f"hotspot-{slug}-{stamp}-{secrets.token_hex(4)}.html"
    if _is_subpath(out_path, scan_root):
        raise RuntimeError(f"Refusing to write HTML report inside scanned repo: {out_path}")

    summary, limits, hotspots = report["summary"], report["limits"], report["hotspots"]
    mermaid_src = build_mermaid_summary(hotspots, repo_name)
    rows_html: list[str] = []
    for item in hotspots:
        st = item["status"]
        badge = "badge-hard" if st == "hard" else ("badge-soft" if st == "soft" else "badge-ok")
        smells_str = ", ".join(f"{k}:{v}" for k, v in item["smells"].items()) or "-"
        prod_pct = min(100, round(item["prod_loc"] * 100 / max(item["total_loc"], 1)))
        rows_html.append(
            f"<tr><td><code>{html.escape(item['path'])}</code></td>"
            f"<td><span class='badge {badge}'>{html.escape(st.upper())}</span></td>"
            f"<td class='num'>{item['prod_loc']}</td><td class='num'>{item['test_loc']}</td>"
            f"<td><div class='bar'><div class='bar-prod' style='width:{prod_pct}%'></div></div></td>"
            f"<td class='num'>{item['churn_commits']}</td>"
            f"<td>{html.escape(smells_str)}</td><td class='num'>{item['risk_score']}</td></tr>"
        )
    if not rows_html:
        rows_html.append("<tr><td colspan='8'>No supported source files found.</td></tr>")

    css = (
        ":root{--bg:#0f172a;--panel:#1e293b;--bd:#334155;--tx:#e2e8f0;--mu:#94a3b8;"
        "--ok:#22c55e;--sf:#f59e0b;--hd:#ef4444;--ac:#38bdf8}"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "background:var(--bg);color:var(--tx);margin:0;padding:24px;line-height:1.5}"
        ".wrap{max-width:1180px;margin:0 auto}h1{margin:0 0 6px;font-size:1.55rem}"
        "h2{margin-top:26px;font-size:1.1rem}.meta{color:var(--mu);font-size:.9rem;margin-bottom:20px}"
        ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:22px}"
        ".card{background:var(--panel);border:1px solid var(--bd);border-radius:8px;padding:12px}"
        ".card .lb{color:var(--mu);font-size:.78rem;text-transform:uppercase}"
        ".card .vl{font-size:1.45rem;font-weight:700;margin-top:4px}"
        "table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--bd);font-size:.9rem}"
        "th,td{padding:9px 11px;border-bottom:1px solid var(--bd);text-align:left}"
        "th{background:#0b1220;color:var(--mu)}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}"
        "code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.85rem}"
        ".badge{padding:2px 8px;border-radius:999px;font-size:.75rem;font-weight:700}"
        ".badge-ok{background:rgba(34,197,94,.15);color:var(--ok)}"
        ".badge-soft{background:rgba(245,158,11,.15);color:var(--sf)}"
        ".badge-hard{background:rgba(239,68,68,.2);color:var(--hd)}"
        ".bar{width:90px;height:8px;background:#334155;border-radius:4px;overflow:hidden}"
        ".bar-prod{height:100%;background:var(--ac)}"
        "pre.mermaid{background:#0b1220;border:1px solid var(--bd);border-radius:8px;padding:16px;overflow-x:auto}"
    )
    html_doc = (
        f"<!DOCTYPE html>\n<html lang='en'>\n<head><meta charset='utf-8' />"
        f"<meta name='viewport' content='width=device-width,initial-scale=1' />"
        f"<title>Architecture Hotspot Report - {html.escape(repo_name)}</title>"
        f"<style>{css}</style></head>\n<body><div class='wrap'>"
        f"<h1>Architecture Hotspot Report: {html.escape(repo_name)}</h1>"
        f"<div class='meta'>Scanned: <code>{html.escape(report['scan_root'])}</code> | "
        f"soft={limits['soft_limit']}L, hard={limits['hard_limit']}L | "
        f"churn={limits['churn_days']}d | UTC {html.escape(stamp)}</div>"
        f"<div class='cards'>"
        f"<div class='card'><div class='lb'>Files</div><div class='vl'>{summary['files_scanned']}</div></div>"
        f"<div class='card'><div class='lb'>Hard Violations</div><div class='vl'>{summary['hard_limit_violations']}</div></div>"
        f"<div class='card'><div class='lb'>Soft Warnings</div><div class='vl'>{summary['soft_limit_warnings']}</div></div>"
        f"<div class='card'><div class='lb'>High-Risk Smells</div><div class='vl'>{summary['total_high_risk_smells']}</div></div>"
        f"<div class='card'><div class='lb'>Prod LOC</div><div class='vl'>{summary['total_prod_loc']}</div></div>"
        f"<div class='card'><div class='lb'>Test LOC</div><div class='vl'>{summary['total_test_loc']}</div></div>"
        f"</div><h2>Top Hotspots</h2><table><thead><tr><th>File</th><th>Status</th>"
        f"<th class='num'>Prod LOC</th><th class='num'>Test LOC</th><th>Prod Share</th>"
        f"<th class='num'>Churn</th><th>Smells</th><th class='num'>Risk</th></tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
        f"<h2>Mermaid Architecture Summary</h2>"
        f'<pre class="mermaid">{html.escape(mermaid_src)}</pre>'
        f"</div></body></html>\n"
    )
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def scan_repository(
    repo_path: Path,
    soft_limit: int = 600,
    hard_limit: int = 1000,
    top: int = 15,
    churn_days: int = 90,
    html_report: bool = False,
) -> tuple[dict[str, Any], int]:
    """Run full hotspot scan and return (report_dict, semantic_exit_code)."""
    scan_root = repo_path.resolve()
    repo_root = find_repo_root(scan_root)
    is_git_repo = repo_root is not None
    files = discover_source_files(scan_root, is_git_repo=is_git_repo)
    churn_map = collect_git_churn(scan_root, repo_root, churn_days=churn_days)

    entries: list[dict[str, Any]] = []
    smell_totals: Counter[str] = Counter()
    for rel_posix, full_path, lang in files:
        entry = analyze_file(
            rel_posix, full_path, lang, churn_map.get(rel_posix, 0), soft_limit, hard_limit
        )
        entries.append(entry)
        smell_totals.update(entry["smells"])

    entries.sort(
        key=lambda e: (-_severity_rank(e), -e["risk_score"], -e["prod_loc"], e["path"])
    )
    hard_v = sum(1 for e in entries if e["status"] == "hard")
    soft_w = sum(1 for e in entries if e["status"] == "soft")
    high_risk_total = sum(e["high_risk_smells"] for e in entries)
    test_files = sum(1 for e in entries if e["is_test_file"])
    has_actionable = hard_v > 0 or high_risk_total > 0

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(repo_root) if repo_root is not None else None,
        "scan_root": str(scan_root),
        "is_git_repo": is_git_repo,
        "limits": {
            "soft_limit": soft_limit, "hard_limit": hard_limit,
            "top": top, "churn_days": churn_days,
        },
        "summary": {
            "files_scanned": len(entries),
            "prod_files": len(entries) - test_files,
            "test_files": test_files,
            "total_prod_loc": sum(e["prod_loc"] for e in entries),
            "total_test_loc": sum(e["test_loc"] for e in entries),
            "hard_limit_violations": hard_v,
            "soft_limit_warnings": soft_w,
            "high_risk_smell_files": sum(1 for e in entries if e["high_risk_smells"] > 0),
            "total_high_risk_smells": high_risk_total,
            "total_smells": sum(e["total_smells"] for e in entries),
            "smell_totals": dict(sorted(smell_totals.items())),
            "exit_reason": "hard_limit_or_high_risk_smells" if has_actionable else "clean",
        },
        "hotspots": entries[:top],
        "html_report_path": None,
    }
    if html_report:
        report["html_report_path"] = str(write_html_report(report, scan_root))
    return report, (1 if has_actionable else 0)


def render_text(report: dict[str, Any]) -> str:
    """Format scan report into a compact human-readable table."""
    s, lim = report["summary"], report["limits"]
    lines = [
        f"Scan root:        {report['scan_root']} ({'git' if report['is_git_repo'] else 'non-git'})",
        f"Thresholds:       soft={lim['soft_limit']}L prod, hard={lim['hard_limit']}L prod, churn={lim['churn_days']}d",
        f"Files scanned:    {s['files_scanned']} (prod={s['prod_files']}, test={s['test_files']})",
        f"LOC totals:       prod={s['total_prod_loc']}, test={s['total_test_loc']}",
        f"Findings:         hard={s['hard_limit_violations']}, soft={s['soft_limit_warnings']}, high_risk_smells={s['total_high_risk_smells']} (total_smells={s['total_smells']})",
    ]
    if s["smell_totals"]:
        lines.append("Smell breakdown:  " + ", ".join(f"{k}={v}" for k, v in s["smell_totals"].items()))
    if report["html_report_path"]:
        lines.append(f"HTML report:      {report['html_report_path']}")
    if report["hotspots"]:
        lines.extend(["", f"{'STATUS':<6} {'PROD':>6} {'TEST':>6} {'CHURN':>5} {'SMELLS':>6}  PATH", "-" * 72])
        for item in report["hotspots"]:
            note = (
                "  [" + ", ".join(f"{k}:{v}" for k, v in item["smells"].items()) + "]"
                if item["smells"]
                else ""
            )
            lines.append(
                f"{item['status'].upper():<6} {item['prod_loc']:>6} {item['test_loc']:>6} "
                f"{item['churn_commits']:>5} {item['total_smells']:>6}  {item['path']}{note}"
            )
    else:
        lines.append("No supported source files found.")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan a repository for monolith hotspots, git churn, and polyglot smells."
    )
    parser.add_argument("--repo", default=".", help="Repository or directory to scan.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output.")
    parser.add_argument("--soft-limit", type=int, default=600, help="Soft prod LOC warning (default: 600).")
    parser.add_argument("--hard-limit", type=int, default=1000, help="Hard prod LOC ceiling (default: 1000).")
    parser.add_argument("--top", type=int, default=15, help="Max ranked hotspots (default: 15).")
    parser.add_argument("--churn-days", type=int, default=90, help="Git churn window in days (default: 90).")
    parser.add_argument("--html-report", action="store_true", help="Write self-contained HTML report to OS temp.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_stdio()
    args = parse_args(argv)
    repo_path = Path(args.repo)
    if not repo_path.exists():
        print(f"ERROR: Path does not exist: {repo_path}", file=sys.stderr)
        return 2
    if not repo_path.is_dir():
        print(f"ERROR: Path is not a directory: {repo_path}", file=sys.stderr)
        return 2
    if args.soft_limit <= 0 or args.hard_limit < args.soft_limit or args.top <= 0 or args.churn_days < 0:
        print("ERROR: Invalid numeric limits (require soft>0, hard>=soft, top>0, churn-days>=0).", file=sys.stderr)
        return 2

    try:
        report, exit_code = scan_repository(
            repo_path, args.soft_limit, args.hard_limit, args.top, args.churn_days, args.html_report
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_text(report))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
