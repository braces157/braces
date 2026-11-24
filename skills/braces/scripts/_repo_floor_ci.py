#!/usr/bin/env python3
"""GitHub Actions workflow hardening and polyglot stack gate checks for repo_floor.py."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

WORKFLOW_USES_RE = re.compile(
    r"^\s*(?:-\s*)?uses\s*:\s*[\"']?([^\"'\s#]+)[\"']?"
)
SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
DOCKER_SHA256_RE = re.compile(r"@sha256:[0-9a-fA-F]{64}$")
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

MANIFEST_SKIP_DIRS = frozenset(
    {
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
)

JS_LINTER_CONFIGS = (
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


def read_text_safe(path: Path) -> str:
    """Read a UTF-8 text file safely without failing on encoding glitches."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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


def _check_uses_pin(action_ref: str, rel_path: str, lineno: int) -> dict[str, Any] | None:
    """Return an unpinned-action finding if action_ref is not pinned immutably."""
    if action_ref.startswith("./") or action_ref.startswith("../"):
        return None
    if action_ref.startswith("docker://"):
        if not DOCKER_SHA256_RE.search(action_ref):
            return make_finding(
                finding_id="workflow-unpinned-action",
                category="github-actions",
                severity="HIGH",
                autonomy="AUTO-FIX",
                path=rel_path,
                line=lineno,
                message=f"Docker action `{action_ref}` is not pinned to a full 64-char sha256 digest.",
                remediation="Pin container actions to an immutable `@sha256:<64-hex-digest>`.",
            )
        return None
    if "@" not in action_ref:
        return make_finding(
            finding_id="workflow-unpinned-action",
            category="github-actions",
            severity="HIGH",
            autonomy="AUTO-FIX",
            path=rel_path,
            line=lineno,
            message=f"Action `{action_ref}` is missing a commit SHA pin.",
            remediation="Pin third-party actions to a full 40-character commit SHA.",
        )
    _, ref = action_ref.rsplit("@", 1)
    if not SHA40_RE.match(ref):
        return make_finding(
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
    return None


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

        has_permissions = False
        overbroad_perm_lines: list[int] = []

        for lineno, raw_line in enumerate(content.splitlines(), start=1):
            code = _strip_yaml_comment(raw_line)
            if not code.strip():
                continue

            if SECRET_SCAN_RE.search(code):
                has_secret_scan = True

            perm_match = TOP_PERMISSIONS_RE.match(code) or ANY_PERMISSIONS_RE.match(code)
            if perm_match:
                has_permissions = True
                if OVERBROAD_PERMISSIONS_RE.search(perm_match.group(1)):
                    overbroad_perm_lines.append(lineno)

            uses_match = WORKFLOW_USES_RE.match(code)
            if uses_match:
                pin_finding = _check_uses_pin(uses_match.group(1).strip(), rel_path, lineno)
                if pin_finding is not None:
                    findings.append(pin_finding)

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
    for name in filenames:
        root_candidate = repo_root / name
        if root_candidate.is_file():
            found.append(root_candidate)
    try:
        for child in repo_root.iterdir():
            if not child.is_dir() or child.name in MANIFEST_SKIP_DIRS or child.name.startswith("."):
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


def _audit_rust_gates(
    repo_root: Path, workflow_text: str, shared_sca: bool
) -> tuple[dict[str, bool], list[dict[str, Any]]] | None:
    manifests = _find_manifests(repo_root, ("Cargo.toml",))
    if not manifests:
        return None
    has_clippy = bool(re.search(r"\bclippy\b", workflow_text, re.IGNORECASE))
    has_fmt = bool(re.search(r"\bcargo\s+fmt\b|\brustfmt\b", workflow_text, re.IGNORECASE))
    has_lint = has_clippy and has_fmt
    has_sca = (
        bool(
            re.search(
                r"\bcargo[- ]audit\b|\bcargo[- ]deny\b|rustsec/audit-check",
                workflow_text,
                re.IGNORECASE,
            )
        )
        or (repo_root / "deny.toml").is_file()
        or shared_sca
    )
    primary = manifests[0].relative_to(repo_root).as_posix()
    findings: list[dict[str, Any]] = []
    if not has_lint:
        findings.append(
            make_finding(
                finding_id="stack-missing-lint",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="AUTO-FIX",
                path=primary,
                message="Rust stack detected, but CI is missing `cargo fmt --check` and/or `cargo clippy`.",
                remediation="Run `cargo fmt --all -- --check` and `cargo clippy --all-targets -- -D warnings` in CI.",
            )
        )
    if not has_sca:
        findings.append(
            make_finding(
                finding_id="stack-missing-sca",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="ASK",
                path=primary,
                message="Rust stack detected, but no SCA gate (`cargo-audit`/`cargo-deny`/Dependabot) was found.",
                remediation="Add `cargo-audit` or `cargo-deny` (or `.github/dependabot.yml`) to track crate advisories.",
            )
        )
    return {"lint": has_lint, "typecheck": True, "sca": has_sca}, findings


def _audit_js_ts_gates(
    repo_root: Path, workflow_text: str, shared_sca: bool
) -> tuple[dict[str, bool], list[dict[str, Any]]] | None:
    manifests = _find_manifests(repo_root, ("package.json",))
    if not manifests:
        return None
    combined_pkg = "\n".join(read_text_safe(p) for p in manifests)
    surface = f"{combined_pkg}\n{workflow_text}"
    has_ts = bool(_find_manifests(repo_root, ("tsconfig.json",))) or bool(
        re.search(r'"typescript"\s*:', combined_pkg)
    )
    has_lint = bool(_find_manifests(repo_root, JS_LINTER_CONFIGS)) or bool(
        re.search(r"\b(?:eslint|oxlint|biome)\b", surface, re.IGNORECASE)
    )
    has_typecheck = True
    if has_ts:
        has_typecheck = bool(
            re.search(
                r"\btsc\b|\btypecheck\b|\btype-check\b|\bnext\s+build\b",
                surface,
                re.IGNORECASE,
            )
        )
    has_sca = (
        bool(re.search(r"\b(?:npm|pnpm|yarn)\s+audit\b", surface, re.IGNORECASE))
        or shared_sca
    )
    primary = manifests[0].relative_to(repo_root).as_posix()
    findings: list[dict[str, Any]] = []
    if not has_lint:
        findings.append(
            make_finding(
                finding_id="stack-missing-lint",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="ASK",
                path=primary,
                message=(
                    "JS/TS stack detected, but no real linter (eslint, oxlint, or biome) "
                    "is configured (`tsc --noEmit` alone is not a linter)."
                ),
                remediation="Add oxlint, biome, or eslint and run it in CI.",
            )
        )
    if not has_typecheck:
        findings.append(
            make_finding(
                finding_id="stack-missing-typecheck",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="AUTO-FIX",
                path=primary,
                message="TypeScript detected, but no `tsc --noEmit` / typecheck step was found.",
                remediation="Add a `tsc --noEmit` script and run it in CI.",
            )
        )
    if not has_sca:
        findings.append(
            make_finding(
                finding_id="stack-missing-sca",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="ASK",
                path=primary,
                message="JS/TS stack detected, but no dependency SCA gate (`npm audit`/Dependabot) was found.",
                remediation="Add `npm audit --audit-level=high` or `.github/dependabot.yml`.",
            )
        )
    return {"lint": has_lint, "typecheck": has_typecheck, "sca": has_sca}, findings


def _audit_python_gates(
    repo_root: Path, workflow_text: str, shared_sca: bool
) -> tuple[dict[str, bool], list[dict[str, Any]]] | None:
    manifests = _find_manifests(repo_root, ("pyproject.toml", "setup.py", "requirements.txt"))
    if not manifests:
        return None
    surface = "\n".join(read_text_safe(p) for p in manifests) + f"\n{workflow_text}"
    has_lint = bool(re.search(r"\b(?:ruff|flake8|pylint)\b", surface, re.IGNORECASE))
    has_typecheck = bool(
        re.search(
            r"\b(?:mypy|pyright|basedpyright)\b|(?:\buvx?\s+run\s+|\b)ty\s+check\b|\[tool\.ty\]",
            surface,
            re.IGNORECASE,
        )
    )
    has_sca = (
        bool(re.search(r"\b(?:pip-audit|safety|bandit)\b", surface, re.IGNORECASE))
        or shared_sca
    )
    primary = manifests[0].relative_to(repo_root).as_posix()
    findings: list[dict[str, Any]] = []
    if not has_lint:
        findings.append(
            make_finding(
                finding_id="stack-missing-lint",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="AUTO-FIX",
                path=primary,
                message="Python stack detected, but no linter (`ruff`, `flake8`, or `pylint`) was found.",
                remediation="Configure `ruff check` and run it in CI.",
            )
        )
    if not has_typecheck:
        findings.append(
            make_finding(
                finding_id="stack-missing-typecheck",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="ASK",
                path=primary,
                message="Python stack detected, but no static type checker (`ty`, `mypy`, or `pyright`) was found.",
                remediation="Add `ty check`, `pyright`, or `mypy` to local checks and CI.",
            )
        )
    if not has_sca:
        findings.append(
            make_finding(
                finding_id="stack-missing-sca",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="ASK",
                path=primary,
                message="Python stack detected, but no SCA/security gate (`pip-audit`/`bandit`/Dependabot) was found.",
                remediation="Add `pip-audit` (and optionally `bandit`) or `.github/dependabot.yml`.",
            )
        )
    return {"lint": has_lint, "typecheck": has_typecheck, "sca": has_sca}, findings


def _audit_java_gates(
    repo_root: Path, workflow_text: str, shared_sca: bool
) -> tuple[dict[str, bool], list[dict[str, Any]]] | None:
    manifests = _find_manifests(repo_root, ("pom.xml", "build.gradle", "build.gradle.kts"))
    if not manifests:
        return None
    surface = "\n".join(read_text_safe(p) for p in manifests) + f"\n{workflow_text}"
    has_lint = bool(
        re.search(
            r"\b(?:spotbugs|checkstyle|pmd|errorprone|qodana|sonar)\b",
            surface,
            re.IGNORECASE,
        )
    )
    has_sca = (
        bool(re.search(r"\b(?:dependency-check|owasp)\b", surface, re.IGNORECASE))
        or shared_sca
    )
    primary = manifests[0].relative_to(repo_root).as_posix()
    findings: list[dict[str, Any]] = []
    if not has_lint:
        findings.append(
            make_finding(
                finding_id="stack-missing-lint",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="ASK",
                path=primary,
                message="Java stack detected, but no static analysis plugin (`spotbugs`, `checkstyle`, or `pmd`) was found.",
                remediation="Configure SpotBugs or Checkstyle/PMD in Maven/Gradle and run in CI.",
            )
        )
    if not has_sca:
        findings.append(
            make_finding(
                finding_id="stack-missing-sca",
                category="stack-gates",
                severity="MEDIUM",
                autonomy="ASK",
                path=primary,
                message="Java stack detected, but no SCA gate (`dependency-check`/Trivy/Dependabot) was found.",
                remediation="Add OWASP dependency-check, Trivy, or `.github/dependabot.yml`.",
            )
        )
    return {"lint": has_lint, "typecheck": True, "sca": has_sca}, findings


def check_stack_gates(
    repo_root: Path, workflow_text: str
) -> tuple[list[str], dict[str, dict[str, bool]], list[dict[str, Any]]]:
    """Detect active languages/stacks and audit lint, typecheck, and SCA gates."""
    detected: list[str] = []
    gates: dict[str, dict[str, bool]] = {}
    findings: list[dict[str, Any]] = []

    shared_sca = _has_dependabot(repo_root) or bool(
        re.search(r"\b(?:codeql|osv-scanner|trivy)\b", workflow_text, re.IGNORECASE)
    )

    auditors = (
        ("rust", _audit_rust_gates),
        ("js_ts", _audit_js_ts_gates),
        ("python", _audit_python_gates),
        ("java", _audit_java_gates),
    )
    for stack_name, auditor in auditors:
        outcome = auditor(repo_root, workflow_text, shared_sca)
        if outcome is not None:
            stack_gate, stack_findings = outcome
            detected.append(stack_name)
            gates[stack_name] = stack_gate
            findings.extend(stack_findings)

    return detected, gates, findings
