#!/usr/bin/env python3
"""Read-only repository security, CI hardening, and hygiene floor checker."""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple, Sequence

sys.dont_write_bytecode = True

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _repo_floor_ci import (  # noqa: E402
    check_github_workflows,
    check_stack_gates,
    make_finding,
    read_text_safe,
)

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

SAFE_ENV_TEMPLATES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
    ".env.defaults",
}

SECRET_EXACT_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
    ".netrc",
}
SECRET_PREFIXES = (".env.", "id_rsa.", "id_ed25519.")
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

ALWAYS_ARTIFACT_DIRS = {
    "node_modules",
    ".codex-run",
    ".audit",
    ".wt",
    ".taste-skill",
    ".vite",
    "__pycache__",
}

AGENT_INSTRUCTION_NAMES = ("AGENTS.md", "GEMINI.md")
LICENSE_STEMS = ("license", "copying", "unlicense")


class CmdResult(NamedTuple):
    """Result from running an external subprocess command."""

    returncode: int
    stdout: str
    stderr: str


def configure_stdio() -> None:
    """Force UTF-8 output streams with replacement to prevent Windows cp1252 crashes."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            reconf(encoding="utf-8", errors="replace")


def run_cmd(cmd: Sequence[str], cwd: Path | None = None) -> CmdResult:
    """Execute a command via argv list without shell expansion."""
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return CmdResult(proc.returncode, proc.stdout, proc.stderr)
    except OSError as exc:
        return CmdResult(127, "", str(exc))


def find_repo_root(start: Path) -> Path | None:
    """Resolve the enclosing git repository root from start path."""
    target = start if start.is_dir() else start.parent
    res = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=target)
    if res.returncode != 0:
        return None
    root = res.stdout.strip()
    return Path(root) if root else None


def list_tracked_files(repo_root: Path) -> tuple[list[str], str | None]:
    """Return tracked POSIX-normalized relative paths via git ls-files -z."""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", "ls-files", "-z"],
            cwd=str(repo_root),
            capture_output=True,
        )
    except OSError as exc:
        return [], f"failed to invoke git: {exc}"
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        return [], err or "git ls-files failed"
    raw = proc.stdout.decode("utf-8", errors="replace")
    files = [
        entry.replace("\\", "/")
        for entry in raw.split("\0")
        if entry
    ]
    return files, None


def _root_entries_map(repo_root: Path) -> dict[str, str]:
    """Map lowercase root filenames to their actual filesystem names."""
    entries: dict[str, str] = {}
    try:
        for child in repo_root.iterdir():
            if child.is_file():
                entries[child.name.lower()] = child.name
    except OSError:
        pass
    return entries


def check_governance_files(repo_root: Path) -> list[dict[str, Any]]:
    """Verify AGENTS.md/GEMINI.md and LICENSE presence at repo root."""
    findings: list[dict[str, Any]] = []
    root_files = _root_entries_map(repo_root)

    if not any(name.lower() in root_files for name in AGENT_INSTRUCTION_NAMES):
        findings.append(
            make_finding(
                finding_id="missing-agent-instructions",
                category="governance",
                severity="MEDIUM",
                autonomy="ASK",
                path="AGENTS.md",
                message="Repository root has neither AGENTS.md nor GEMINI.md.",
                remediation=(
                    "Add a concise AGENTS.md documenting build/test commands, "
                    "invariants, and project-specific footguns."
                ),
            )
        )

    has_license = False
    for lower_name in root_files:
        stem = Path(lower_name).stem.lower()
        if stem in LICENSE_STEMS or lower_name in LICENSE_STEMS:
            has_license = True
            break
    if not has_license:
        findings.append(
            make_finding(
                finding_id="missing-license",
                category="governance",
                severity="MEDIUM",
                autonomy="ASK",
                path="LICENSE",
                message="Repository root is missing a LICENSE file.",
                remediation=(
                    "Confirm the desired license with the owner (e.g. MIT or "
                    "Apache-2.0) and add LICENSE at the repository root."
                ),
            )
        )

    return findings


def _normalize_gitignore_rule(raw_line: str) -> str | None:
    """Return normalized positive .gitignore pattern, or None for comments/negations."""
    line = raw_line.strip()
    if not line or line.startswith("#") or line.startswith("!"):
        return None
    return line


def _pattern_matches_target(pattern: str, sample_name: str, is_dir: bool) -> bool:
    """Check whether a single .gitignore rule matches a root file or directory."""
    pat = pattern.strip()
    if pat.startswith("**/"):
        pat = pat[3:]
    if pat.startswith("/"):
        pat = pat[1:]

    if is_dir:
        dir_name = sample_name.rstrip("/")
        candidates = (
            dir_name,
            f"{dir_name}/",
            f"{dir_name}/*",
            f"{dir_name}/**",
        )
        if pat in candidates:
            return True
        if pat.endswith("/") and fnmatch.fnmatch(dir_name, pat.rstrip("/")):
            return True
        return fnmatch.fnmatch(dir_name, pat) or fnmatch.fnmatch(f"{dir_name}/x", pat)

    if pat.endswith("/"):
        return False
    return fnmatch.fnmatch(sample_name, pat)


def check_gitignore_coverage(repo_root: Path) -> list[dict[str, Any]]:
    """Verify .gitignore exists and covers .env*, .audit/, .wt/, and .taste-skill/."""
    gitignore_path = repo_root / ".gitignore"
    if not gitignore_path.is_file():
        return [
            make_finding(
                finding_id="missing-gitignore",
                category="hygiene",
                severity="HIGH",
                autonomy="AUTO-FIX",
                path=".gitignore",
                message="Repository root is missing a .gitignore file.",
                remediation=(
                    "Create .gitignore covering .env*, .audit/, .wt/, "
                    ".taste-skill/, and stack build artifacts."
                ),
            )
        ]

    rules: list[str] = []
    for raw_line in read_text_safe(gitignore_path).splitlines():
        norm = _normalize_gitignore_rule(raw_line)
        if norm is not None:
            rules.append(norm)

    missing: list[str] = []

    env_root_covered = any(_pattern_matches_target(r, ".env", False) for r in rules)
    env_local_covered = any(
        _pattern_matches_target(r, ".env.local", False) for r in rules
    )
    if not (env_root_covered and env_local_covered):
        missing.append(".env*")

    for required_dir in (".audit/", ".wt/", ".taste-skill/"):
        if not any(_pattern_matches_target(r, required_dir, True) for r in rules):
            missing.append(required_dir)

    if not missing:
        return []

    return [
        make_finding(
            finding_id="gitignore-coverage",
            category="hygiene",
            severity="MEDIUM",
            autonomy="AUTO-FIX",
            path=".gitignore",
            message=f".gitignore is missing required hygiene rules: {', '.join(missing)}.",
            remediation=(
                f"Add {', '.join(missing)} to .gitignore "
                "(optionally keep !.env.example if using an env template)."
            ),
        )
    ]


def _is_secret_tracked_path(rel_path: str) -> bool:
    """Return True if a tracked path looks like a secret/credential file."""
    basename = Path(rel_path).name.lower()
    if basename in SAFE_ENV_TEMPLATES:
        return False
    if basename in SECRET_EXACT_NAMES:
        return True
    if any(basename.startswith(prefix) for prefix in SECRET_PREFIXES):
        return True
    if any(basename.endswith(suffix) for suffix in SECRET_SUFFIXES):
        return True
    return False


def _artifact_root_for_path(rel_path: str, repo_root: Path) -> str | None:
    """Return tracked artifact directory prefix if rel_path is under build/agent clutter."""
    parts = [p for p in rel_path.split("/") if p]
    if len(parts) < 2:
        return None
    for idx, part in enumerate(parts[:-1]):
        lower = part.lower()
        if lower in ALWAYS_ARTIFACT_DIRS:
            return "/".join(parts[: idx + 1]) + "/"
        if lower == "target":
            if idx == 0:
                return "target/"
            parent_dir = repo_root.joinpath(*parts[:idx])
            if (parent_dir / "Cargo.toml").is_file() or (parent_dir / "pom.xml").is_file():
                return "/".join(parts[: idx + 1]) + "/"
    return None


def check_tracked_files(
    repo_root: Path, tracked_files: Sequence[str]
) -> list[dict[str, Any]]:
    """Flag tracked secret files and build/agent artifact directories without reading secrets."""
    findings: list[dict[str, Any]] = []
    artifact_buckets: dict[str, list[str]] = {}

    for rel_path in tracked_files:
        if _is_secret_tracked_path(rel_path):
            findings.append(
                make_finding(
                    finding_id="tracked-secret-file",
                    category="secrets",
                    severity="CRITICAL",
                    autonomy="ASK",
                    path=rel_path,
                    message=(
                        f"Potentially sensitive file is tracked by git: {rel_path} "
                        "(contents not read)."
                    ),
                    remediation=(
                        "Rotate any exposed credentials, untrack with `git rm --cached`, "
                        "add to .gitignore, and scrub git history if already pushed."
                    ),
                )
            )
            continue

        artifact_root = _artifact_root_for_path(rel_path, repo_root)
        if artifact_root is not None:
            artifact_buckets.setdefault(artifact_root, []).append(rel_path)

    for artifact_root in sorted(artifact_buckets):
        paths = artifact_buckets[artifact_root]
        sample = ", ".join(paths[:3])
        extra = f" (+{len(paths) - 3} more)" if len(paths) > 3 else ""
        findings.append(
            make_finding(
                finding_id="tracked-artifact-dir",
                category="hygiene",
                severity="HIGH",
                autonomy="ASK",
                path=artifact_root,
                message=(
                    f"Tracked build or agent artifact directory `{artifact_root}` "
                    f"contains {len(paths)} tracked file(s): {sample}{extra}."
                ),
                remediation=(
                    f"Untrack `{artifact_root}` (`git rm -r --cached`) and ignore it in .gitignore."
                ),
            )
        )

    return findings


def audit_repo_floor(repo_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Run all read-only repo-floor checks and return a structured report."""
    tracked_files, err = list_tracked_files(repo_root)
    if err is not None:
        return None, err

    findings: list[dict[str, Any]] = []
    findings.extend(check_governance_files(repo_root))
    findings.extend(check_gitignore_coverage(repo_root))
    findings.extend(check_tracked_files(repo_root, tracked_files))

    wf_findings, wf_text = check_github_workflows(repo_root)
    findings.extend(wf_findings)

    detected_stacks, stack_gates, stack_findings = check_stack_gates(
        repo_root, wf_text
    )
    findings.extend(stack_findings)

    findings.sort(
        key=lambda f: (
            SEVERITY_ORDER.get(f["severity"], 99),
            f["category"],
            f["path"],
            f.get("line", 0),
        )
    )

    by_severity = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for item in findings:
        sev = item["severity"]
        if sev in by_severity:
            by_severity[sev] += 1

    report: dict[str, Any] = {
        "status": "clean" if not findings else "findings",
        "repo_root": str(repo_root),
        "detected_stacks": detected_stacks,
        "stack_gates": stack_gates,
        "summary": {
            "total_findings": len(findings),
            "by_severity": by_severity,
        },
        "findings": findings,
    }
    return report, None


def render_text(report: dict[str, Any]) -> str:
    """Render concise human-readable repo floor audit output."""
    lines: list[str] = []
    summary = report["summary"]
    counts = summary["by_severity"]
    lines.append(f"Repo Floor Audit: {report['status'].upper()}")
    lines.append(f"Repository: {report['repo_root']}")
    lines.append(
        "Findings: "
        f"{summary['total_findings']} "
        f"(CRITICAL={counts['CRITICAL']}, HIGH={counts['HIGH']}, "
        f"MEDIUM={counts['MEDIUM']}, LOW={counts['LOW']})"
    )

    stacks = report["detected_stacks"]
    if stacks:
        lines.append("")
        lines.append("Detected Stack Gates:")
        for stack in stacks:
            g = report["stack_gates"][stack]
            lint_tag = "[OK]" if g["lint"] else "[GAP]"
            type_tag = "[OK]" if g["typecheck"] else "[GAP]"
            sca_tag = "[OK]" if g["sca"] else "[GAP]"
            lines.append(
                f"  - {stack}: lint={lint_tag} typecheck={type_tag} sca={sca_tag}"
            )

    findings = report["findings"]
    if findings:
        lines.append("")
        lines.append("Findings:")
        for idx, item in enumerate(findings, start=1):
            loc = f"{item['path']}:{item['line']}" if "line" in item else item["path"]
            lines.append(
                f"  {idx}. [{item['severity']}] [{item['autonomy']}] {item['id']} ({loc})"
            )
            lines.append(f"     {item['message']}")
            lines.append(f"     Fix: {item['remediation']}")
    else:
        lines.append("")
        lines.append("All repository floor checks passed.")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Audit repository security, CI hardening, and hygiene floor (read-only)."
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Path inside the target git repository (default: current directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Exit 0 = clean, 1 = findings, 2 = CLI/repo error."""
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    target = Path(args.repo).expanduser()
    if not target.exists():
        print(f"error: repository path does not exist: {target}", file=sys.stderr)
        return 2

    repo_root = find_repo_root(target.resolve())
    if repo_root is None:
        print(f"error: not a git repository: {target}", file=sys.stderr)
        return 2

    report, err = audit_repo_floor(repo_root)
    if err is not None or report is None:
        print(f"error: {err or 'repo floor audit failed'}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))

    return 0 if report["summary"]["total_findings"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
