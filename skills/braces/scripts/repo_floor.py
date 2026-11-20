#!/usr/bin/env python3
"""Read-only repository security, CI hardening, and hygiene floor checker."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple, Sequence

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

WORKFLOW_USES_RE = re.compile(
    r"^\s*(?:-\s*)?uses\s*:\s*[\"']?([^\"'\s#]+)[\"']?"
)
SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
TOP_PERMISSIONS_RE = re.compile(r"^permissions\s*:(.*)$")
ANY_PERMISSIONS_RE = re.compile(r"^\s+permissions\s*:(.*)$")
OVERBROAD_PERMISSIONS_RE = re.compile(r"\bwrite-all\b", re.IGNORECASE)

TEST_SKIP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "-DskipTests",
        re.compile(r"(?<!\w)-DskipTests(?:=true)?(?![\w=])", re.IGNORECASE),
    ),
    (
        "-Dmaven.test.skip=true",
        re.compile(r"(?<!\w)-Dmaven\.test\.skip(?:=true)?(?![\w=])", re.IGNORECASE),
    ),
    (
        "--no-verify",
        re.compile(r"(?:^|[\s\"'`])--no-verify(?=$|[\s\"'`])"),
    ),
    (
        "gradle -x test",
        re.compile(r"(?:^|\s)-x\s+test(?=$|\s)"),
    ),
)

SECRET_SCAN_RE = re.compile(
    r"\b(?:gitleaks|trufflehog|detect-secrets|secretlint|git-secrets)\b",
    re.IGNORECASE,
)


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


def read_text_safe(path: Path) -> str:
    """Read a UTF-8 text file safely without failing on encoding glitches."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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


def make_finding(
    finding_id: str,
    category: str,
    severity: str,
    autonomy: str,
    path: str,
    message: str,
    remediation: str,
    line: int | None = None,
) -> dict[str, Any]:
    """Build a structured finding record without exposing secret contents."""
    item: dict[str, Any] = {
        "id": finding_id,
        "category": category,
        "severity": severity,
        "autonomy": autonomy,
        "path": path,
        "message": message,
        "remediation": remediation,
    }
    if line is not None:
        item["line"] = line
    return item


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


def _strip_yaml_comment(line: str) -> str:
    """Strip unquoted YAML comments from a single line."""
    in_single = False
    in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or line[idx - 1].isspace():
                return line[:idx]
    return line


def _list_workflow_files(repo_root: Path) -> list[Path]:
    """Return sorted GitHub Actions workflow files under .github/workflows."""
    wf_dir = repo_root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    files: list[Path] = []
    for ext in ("*.yml", "*.yaml"):
        files.extend(p for p in wf_dir.glob(ext) if p.is_file())
    return sorted(set(files), key=lambda p: p.name.lower())


def check_github_workflows(
    repo_root: Path,
) -> tuple[list[dict[str, Any]], str]:
    """Audit .github/workflows/*.y*ml for permissions, SHA pinning, test skips, and secret scans."""
    workflows = _list_workflow_files(repo_root)
    if not workflows:
        return (
            [
                make_finding(
                    finding_id="missing-ci-workflows",
                    category="github-actions",
                    severity="MEDIUM",
                    autonomy="ASK",
                    path=".github/workflows/",
                    message="No GitHub Actions workflow files found under .github/workflows/.",
                    remediation=(
                        "Add a least-privilege CI workflow with `permissions: contents: read`, "
                        "SHA-pinned actions, tests, linters, and secret scanning."
                    ),
                )
            ],
            "",
        )

    findings: list[dict[str, Any]] = []
    combined_text_parts: list[str] = []
    has_secret_scan = False

    for wf_path in workflows:
        rel_path = wf_path.relative_to(repo_root).as_posix()
        content = read_text_safe(wf_path)
        combined_text_parts.append(content)
        lines = content.splitlines()

        has_permissions = False
        overbroad_perm_lines: list[int] = []

        for lineno, raw_line in enumerate(lines, start=1):
            code = _strip_yaml_comment(raw_line)
            if not code.strip():
                continue

            if SECRET_SCAN_RE.search(code):
                has_secret_scan = True

            top_match = TOP_PERMISSIONS_RE.match(code)
            any_match = ANY_PERMISSIONS_RE.match(code)
            if top_match or any_match:
                has_permissions = True
                val = (top_match or any_match).group(1) if (top_match or any_match) else ""
                if OVERBROAD_PERMISSIONS_RE.search(val):
                    overbroad_perm_lines.append(lineno)

            uses_match = WORKFLOW_USES_RE.match(code)
            if uses_match:
                action_ref = uses_match.group(1).strip()
                if action_ref.startswith("./") or action_ref.startswith("../"):
                    pass
                elif action_ref.startswith("docker://"):
                    if "@sha256:" not in action_ref.lower():
                        findings.append(
                            make_finding(
                                finding_id="workflow-unpinned-action",
                                category="github-actions",
                                severity="HIGH",
                                autonomy="AUTO-FIX",
                                path=rel_path,
                                line=lineno,
                                message=f"Docker action `{action_ref}` is not pinned to a sha256 digest.",
                                remediation="Pin container actions to an immutable `@sha256:<digest>`.",
                            )
                        )
                else:
                    if "@" not in action_ref:
                        findings.append(
                            make_finding(
                                finding_id="workflow-unpinned-action",
                                category="github-actions",
                                severity="HIGH",
                                autonomy="AUTO-FIX",
                                path=rel_path,
                                line=lineno,
                                message=f"Action `{action_ref}` is missing a commit SHA pin.",
                                remediation="Pin third-party actions to a full 40-character commit SHA.",
                            )
                        )
                    else:
                        _, ref = action_ref.rsplit("@", 1)
                        if not SHA40_RE.match(ref):
                            findings.append(
                                make_finding(
                                    finding_id="workflow-unpinned-action",
                                    category="github-actions",
                                    severity="HIGH",
                                    autonomy="AUTO-FIX",
                                    path=rel_path,
                                    line=lineno,
                                    message=(
                                        f"Action `{action_ref}` uses mutable ref `{ref}` "
                                        "instead of a 40-char commit SHA."
                                    ),
                                    remediation=(
                                        "Resolve the verified commit SHA for this release tag and "
                                        "pin `owner/repo@<40-char-sha> # <tag>`."
                                    ),
                                )
                            )

            for signal_name, pattern in TEST_SKIP_PATTERNS:
                if pattern.search(code):
                    findings.append(
                        make_finding(
                            finding_id="workflow-test-skip-bypass",
                            category="github-actions",
                            severity="CRITICAL",
                            autonomy="ASK",
                            path=rel_path,
                            line=lineno,
                            message=f"CI workflow contains test/hook bypass signal `{signal_name}`.",
                            remediation=(
                                "Remove test-skipping flags from CI and fail the build if tests fail or are skipped."
                            ),
                        )
                    )

        if not has_permissions:
            findings.append(
                make_finding(
                    finding_id="workflow-missing-permissions",
                    category="github-actions",
                    severity="HIGH",
                    autonomy="AUTO-FIX",
                    path=rel_path,
                    message="Workflow does not declare an explicit `permissions:` block.",
                    remediation=(
                        "Add top-level `permissions: { contents: read }` and elevate individual jobs only when needed."
                    ),
                )
            )
        for lineno in overbroad_perm_lines:
            findings.append(
                make_finding(
                    finding_id="workflow-overbroad-permissions",
                    category="github-actions",
                    severity="HIGH",
                    autonomy="ASK",
                    path=rel_path,
                    line=lineno,
                    message="Workflow grants overbroad `permissions: write-all`.",
                    remediation="Scope `permissions:` to least privilege (`contents: read` at top level).",
                )
            )

    if not has_secret_scan:
        findings.append(
            make_finding(
                finding_id="workflow-missing-secret-scan",
                category="github-actions",
                severity="MEDIUM",
                autonomy="ASK",
                path=".github/workflows/",
                message="No GitHub Actions workflow runs secret scanning (e.g. gitleaks).",
                remediation=(
                    "Add a SHA-pinned secret-scanning job (such as gitleaks) to CI."
                ),
            )
        )

    return findings, "\n".join(combined_text_parts)


def _find_manifests(repo_root: Path, filenames: Sequence[str]) -> list[Path]:
    """Locate manifest files at repo root or one directory level below."""
    found: list[Path] = []
    skip_dirs = {
        ".git",
        "node_modules",
        "target",
        "dist",
        "build",
        ".venv",
        "venv",
        ".wt",
        ".audit",
        ".codex-run",
        ".taste-skill",
    }
    for name in filenames:
        root_candidate = repo_root / name
        if root_candidate.is_file():
            found.append(root_candidate)
    try:
        for child in repo_root.iterdir():
            if not child.is_dir() or child.name in skip_dirs or child.name.startswith("."):
                continue
            for name in filenames:
                sub_candidate = child / name
                if sub_candidate.is_file():
                    found.append(sub_candidate)
    except OSError:
        pass
    return sorted(set(found), key=lambda p: p.as_posix().lower())


def _has_dependabot(repo_root: Path) -> bool:
    """Return True if Dependabot config is present."""
    return (repo_root / ".github" / "dependabot.yml").is_file() or (
        repo_root / ".github" / "dependabot.yaml"
    ).is_file()


def check_stack_gates(
    repo_root: Path, workflow_text: str
) -> tuple[list[str], dict[str, dict[str, bool]], list[dict[str, Any]]]:
    """Detect active languages/stacks and audit lint, typecheck, and SCA gates."""
    detected: list[str] = []
    gates: dict[str, dict[str, bool]] = {}
    findings: list[dict[str, Any]] = []

    has_dependabot = _has_dependabot(repo_root)
    has_codeql_or_sca = bool(
        re.search(r"\b(?:codeql|osv-scanner|trivy)\b", workflow_text, re.IGNORECASE)
    )

    # 1. Rust
    rust_manifests = _find_manifests(repo_root, ("Cargo.toml",))
    if rust_manifests:
        detected.append("rust")
        has_clippy = bool(re.search(r"\bclippy\b", workflow_text, re.IGNORECASE))
        has_fmt = bool(
            re.search(r"\bcargo\s+fmt\b|\brustfmt\b", workflow_text, re.IGNORECASE)
        )
        has_rust_lint = has_clippy and has_fmt
        has_rust_sca = (
            bool(
                re.search(
                    r"\bcargo[- ]audit\b|\bcargo[- ]deny\b|rustsec/audit-check",
                    workflow_text,
                    re.IGNORECASE,
                )
            )
            or (repo_root / "deny.toml").is_file()
            or has_dependabot
            or has_codeql_or_sca
        )
        gates["rust"] = {
            "lint": has_rust_lint,
            "typecheck": True,  # Rust compiler enforces static types
            "sca": has_rust_sca,
        }
        if not has_rust_lint:
            findings.append(
                make_finding(
                    finding_id="stack-missing-lint",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="AUTO-FIX",
                    path=rust_manifests[0].relative_to(repo_root).as_posix(),
                    message="Rust stack detected, but CI is missing `cargo fmt --check` and/or `cargo clippy`.",
                    remediation="Run `cargo fmt --all -- --check` and `cargo clippy --all-targets -- -D warnings` in CI.",
                )
            )
        if not has_rust_sca:
            findings.append(
                make_finding(
                    finding_id="stack-missing-sca",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="ASK",
                    path=rust_manifests[0].relative_to(repo_root).as_posix(),
                    message="Rust stack detected, but no SCA gate (`cargo-audit`/`cargo-deny`/Dependabot) was found.",
                    remediation="Add `cargo-audit` or `cargo-deny` (or `.github/dependabot.yml`) to track crate advisories.",
                )
            )

    # 2. JS / TS
    pkg_manifests = _find_manifests(repo_root, ("package.json",))
    if pkg_manifests:
        detected.append("js_ts")
        pkg_texts = [read_text_safe(p) for p in pkg_manifests]
        combined_pkg = "\n".join(pkg_texts)
        has_ts = bool(_find_manifests(repo_root, ("tsconfig.json",))) or bool(
            re.search(r'"typescript"\s*:', combined_pkg)
        )

        # Check for real JS/TS linter (tsc --noEmit alone is NOT a linter)
        linter_configs = (
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.cjs",
            "eslint.config.ts",
            ".eslintrc",
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.json",
            ".eslintrc.yml",
            "biome.json",
            "biome.jsonc",
            ".oxlintrc.json",
            "oxlint.json",
        )
        has_linter_file = bool(_find_manifests(repo_root, linter_configs))
        has_linter_ref = bool(
            re.search(
                r"\b(?:eslint|oxlint|biome)\b",
                f"{combined_pkg}\n{workflow_text}",
                re.IGNORECASE,
            )
        )
        has_js_lint = has_linter_file or has_linter_ref

        has_js_typecheck = True
        if has_ts:
            has_js_typecheck = bool(
                re.search(
                    r"\btsc\b|\btypecheck\b|\btype-check\b|\bnext\s+build\b",
                    f"{combined_pkg}\n{workflow_text}",
                    re.IGNORECASE,
                )
            )

        has_js_sca = (
            bool(
                re.search(
                    r"\b(?:npm|pnpm|yarn)\s+audit\b",
                    f"{combined_pkg}\n{workflow_text}",
                    re.IGNORECASE,
                )
            )
            or has_dependabot
            or has_codeql_or_sca
        )

        gates["js_ts"] = {
            "lint": has_js_lint,
            "typecheck": has_js_typecheck,
            "sca": has_js_sca,
        }
        primary_pkg = pkg_manifests[0].relative_to(repo_root).as_posix()
        if not has_js_lint:
            findings.append(
                make_finding(
                    finding_id="stack-missing-lint",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="ASK",
                    path=primary_pkg,
                    message=(
                        "JS/TS stack detected, but no real linter (eslint, oxlint, or biome) "
                        "is configured (`tsc --noEmit` alone is not a linter)."
                    ),
                    remediation="Add oxlint, biome, or eslint and run it in CI.",
                )
            )
        if not has_js_typecheck:
            findings.append(
                make_finding(
                    finding_id="stack-missing-typecheck",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="AUTO-FIX",
                    path=primary_pkg,
                    message="TypeScript detected, but no `tsc --noEmit` / typecheck step was found.",
                    remediation="Add a `tsc --noEmit` script and run it in CI.",
                )
            )
        if not has_js_sca:
            findings.append(
                make_finding(
                    finding_id="stack-missing-sca",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="ASK",
                    path=primary_pkg,
                    message="JS/TS stack detected, but no dependency SCA gate (`npm audit`/Dependabot) was found.",
                    remediation="Add `npm audit --audit-level=high` or `.github/dependabot.yml`.",
                )
            )

    # 3. Python
    py_manifests = _find_manifests(
        repo_root, ("pyproject.toml", "setup.py", "requirements.txt")
    )
    if py_manifests:
        detected.append("python")
        py_texts = "\n".join(read_text_safe(p) for p in py_manifests)
        py_surface = f"{py_texts}\n{workflow_text}"
        has_py_lint = bool(
            re.search(r"\b(?:ruff|flake8|pylint)\b", py_surface, re.IGNORECASE)
        )
        has_py_typecheck = bool(
            re.search(
                r"\b(?:mypy|pyright|basedpyright)\b|(?:\buvx?\s+run\s+|\b)ty\s+check\b|\[tool\.ty\]",
                py_surface,
                re.IGNORECASE,
            )
        )
        has_py_sca = (
            bool(
                re.search(
                    r"\b(?:pip-audit|safety|bandit)\b",
                    py_surface,
                    re.IGNORECASE,
                )
            )
            or has_dependabot
            or has_codeql_or_sca
        )
        gates["python"] = {
            "lint": has_py_lint,
            "typecheck": has_py_typecheck,
            "sca": has_py_sca,
        }
        primary_py = py_manifests[0].relative_to(repo_root).as_posix()
        if not has_py_lint:
            findings.append(
                make_finding(
                    finding_id="stack-missing-lint",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="AUTO-FIX",
                    path=primary_py,
                    message="Python stack detected, but no linter (`ruff`, `flake8`, or `pylint`) was found.",
                    remediation="Configure `ruff check` and run it in CI.",
                )
            )
        if not has_py_typecheck:
            findings.append(
                make_finding(
                    finding_id="stack-missing-typecheck",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="ASK",
                    path=primary_py,
                    message="Python stack detected, but no static type checker (`ty`, `mypy`, or `pyright`) was found.",
                    remediation="Add `ty check`, `pyright`, or `mypy` to local checks and CI.",
                )
            )
        if not has_py_sca:
            findings.append(
                make_finding(
                    finding_id="stack-missing-sca",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="ASK",
                    path=primary_py,
                    message="Python stack detected, but no SCA/security gate (`pip-audit`/`bandit`/Dependabot) was found.",
                    remediation="Add `pip-audit` (and optionally `bandit`) or `.github/dependabot.yml`.",
                )
            )

    # 4. Java / Maven / Gradle
    java_manifests = _find_manifests(
        repo_root, ("pom.xml", "build.gradle", "build.gradle.kts")
    )
    if java_manifests:
        detected.append("java")
        java_texts = "\n".join(read_text_safe(p) for p in java_manifests)
        java_surface = f"{java_texts}\n{workflow_text}"
        has_java_lint = bool(
            re.search(
                r"\b(?:spotbugs|checkstyle|pmd|errorprone|qodana|sonar)\b",
                java_surface,
                re.IGNORECASE,
            )
        )
        has_java_sca = (
            bool(
                re.search(
                    r"\b(?:dependency-check|owasp)\b",
                    java_surface,
                    re.IGNORECASE,
                )
            )
            or has_dependabot
            or has_codeql_or_sca
        )
        gates["java"] = {
            "lint": has_java_lint,
            "typecheck": True,  # javac enforces static types
            "sca": has_java_sca,
        }
        primary_java = java_manifests[0].relative_to(repo_root).as_posix()
        if not has_java_lint:
            findings.append(
                make_finding(
                    finding_id="stack-missing-lint",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="ASK",
                    path=primary_java,
                    message="Java stack detected, but no static analysis plugin (`spotbugs`, `checkstyle`, or `pmd`) was found.",
                    remediation="Configure SpotBugs or Checkstyle/PMD in Maven/Gradle and run in CI.",
                )
            )
        if not has_java_sca:
            findings.append(
                make_finding(
                    finding_id="stack-missing-sca",
                    category="stack-gates",
                    severity="MEDIUM",
                    autonomy="ASK",
                    path=primary_java,
                    message="Java stack detected, but no SCA gate (`dependency-check`/Trivy/Dependabot) was found.",
                    remediation="Add OWASP dependency-check, Trivy, or `.github/dependabot.yml`.",
                )
            )

    return detected, gates, findings


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
