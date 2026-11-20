#!/usr/bin/env python3
"""Classify git diff risk tiers, newly added bypasses/hazards, and removed guards."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Sequence

FENCE_BEGIN = "--- BEGIN UNTRUSTED DIFF ---"
FENCE_END = "--- END UNTRUSTED DIFF ---"

TIER_RANK = {"STANDARD": 0, "HIGH": 1, "CRITICAL": 2}
MAX_SNIPPET_CHARS = 240

HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$"
)

CRITICAL_PATH_RE = re.compile(
    r"(?:^|/)(?:"
    r"[^/]*(?:auth|oauth|jwt|session|security|csrf|cors|rbac|acl|"
    r"permission|crypto|cipher|tls|ssl|cert|secret|token|passwd|"
    r"password|login|signin|signup|middleware|guard|policy|sanitize|"
    r"xss|ssrf|firewall)[^/]*"
    r"|\.env(?:\..+)?$"
    r"|.*\.(?:pem|key|p12|pfx)$"
    r")$",
    re.IGNORECASE,
)
SAFE_ENV_BASENAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
    ".env.defaults",
}
WORKFLOW_PATH_RE = re.compile(r"^\.github/(?:workflows/.*\.ya?ml|actions/.*)$", re.IGNORECASE)

HIGH_PATH_RE = re.compile(
    r"(?:^|/)(?:"
    r"pom\.xml|build\.gradle(?:\.kts)?|Cargo\.toml|package\.json|"
    r"pyproject\.toml|setup\.py|Makefile|Dockerfile[^/]*|"
    r"docker-compose[^/]*\.ya?ml"
    r"|.*\.(?:ps1|sh|bat|cmd)$"
    r"|[^/]*(?:http|client|server|api|route|controller|handler|"
    r"ffi|win32|unsafe|sql|database|migration|flyway|parser|"
    r"scraper|upload|download|subprocess|sandbox|installer|packaging)[^/]*"
    r")$",
    re.IGNORECASE,
)

SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
WORKFLOW_USES_RE = re.compile(
    r"^\s*(?:-\s*)?uses\s*:\s*[\"']?([^\"'\s#]+)[\"']?"
)

SECRET_ASSIGN_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|secret[_-]?key|rememberMeKey|private[_-]?key|"
    r"password|passwd|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*)"
    r"([\"'])([^\"'\r\n]{8,})\2"
)
PLACEHOLDER_SECRET_RE = re.compile(
    r"(?i)(?:changeme|example|placeholder|your[-_]|dummy|sample|test[-_]|"
    r"replace[-_]|redacted|\$\{|process\.env|os\.environ|env\.|xxxxx)"
)

ADDED_SIGNAL_RULES: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "test-skip-maven",
        "CRITICAL",
        "ASK",
        re.compile(
            r"(?<!\w)(?:-DskipTests|-Dmaven\.test\.skip)(?:=true)?(?![\w=])",
            re.IGNORECASE,
        ),
    ),
    (
        "git-no-verify",
        "CRITICAL",
        "ASK",
        re.compile(r"(?:^|[\s\"'`])--no-verify(?=$|[\s\"'`])"),
    ),
    (
        "rust-test-ignore",
        "HIGH",
        "ASK",
        re.compile(r"#\[\s*ignore\b"),
    ),
    (
        "js-test-skip",
        "HIGH",
        "ASK",
        re.compile(r"\b(?:it|test|describe|suite)\.skip\s*\(|\bx(?:it|test|describe)\s*\("),
    ),
    (
        "java-test-disabled",
        "HIGH",
        "ASK",
        re.compile(r"@(?:Disabled|Ignore)\b"),
    ),
    (
        "python-test-skip",
        "HIGH",
        "ASK",
        re.compile(r"@(?:pytest\.mark|unittest)\.skip\b"),
    ),
    (
        "spring-csrf-disable",
        "CRITICAL",
        "ASK",
        re.compile(
            r"\.csrf\s*\(\s*\)\s*\.disable\s*\(\s*\)"
            r"|\bcsrf\s*\(\s*(?:AbstractHttpConfigurer::disable|[^)]*\.disable\s*\(\s*\))\s*\)"
            r"|\bcsrf\s*->\s*csrf\.disable\s*\(\s*\)"
        ),
    ),
    (
        "spring-wide-permit-all",
        "CRITICAL",
        "ASK",
        re.compile(
            r"(?:swagger-ui|v3/api-docs|/ws/\*\*|/uploads/\*\*|/\*\*|anyRequest\s*\(\s*\))"
            r".*\.permitAll\s*\(\s*\)",
            re.IGNORECASE,
        ),
    ),
    (
        "rust-lock-unwrap",
        "HIGH",
        "ASK",
        re.compile(r"\.lock\s*\(\s*\)\s*\.(?:unwrap|expect)\s*\("),
    ),
    (
        "rust-unsafe-block",
        "HIGH",
        "ASK",
        re.compile(r"\bunsafe\s*(?:\{|fn\b|impl\b|trait\b)"),
    ),
    (
        "python-shell-true",
        "CRITICAL",
        "ASK",
        re.compile(r"\bshell\s*=\s*True\b"),
    ),
    (
        "python-bare-except",
        "HIGH",
        "ASK",
        re.compile(r"^\s*except\s*:\s*(?:#.*)?$"),
    ),
    (
        "tls-verify-disabled",
        "CRITICAL",
        "ASK",
        re.compile(
            r"rejectUnauthorized\s*:\s*false"
            r"|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*[\"']?0"
            r"|danger_accept_invalid_certs\s*\(\s*true"
            r"|\bverify\s*=\s*False\b"
            r"|InsecureSkipVerify\s*:\s*true",
            re.IGNORECASE,
        ),
    ),
    (
        "xss-sink",
        "HIGH",
        "ASK",
        re.compile(r"\bdangerouslySetInnerHTML\b|\binnerHTML\s*=|\bdocument\.write\s*\("),
    ),
    (
        "ts-type-escape",
        "HIGH",
        "ASK",
        re.compile(r"@ts-ignore\b|@ts-nocheck\b"),
    ),
)

REMOVED_GUARD_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "auth",
        "CRITICAL",
        re.compile(
            r"\b(?:auth\w*|requireAuth|isAuthenticated|authenticated|authorize|"
            r"hasRole|hasAuthority|checkAuth|verifyAuth|ensureAuth|PreAuthorize)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "csrf",
        "CRITICAL",
        re.compile(r"\b(?:csrf\w*|CsrfToken|X-CSRF-Token)\b", re.IGNORECASE),
    ),
    (
        "verify",
        "HIGH",
        re.compile(
            r"\b(?:verify\w*|validate\w*|checkPermission|checkAccess|"
            r"checkOrigin|checkHost|verifySignature|verifyToken)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "assert",
        "HIGH",
        re.compile(
            r"\b(?:assert|assert_eq|assert_ne|debug_assert|requireNonNull|"
            r"checkArgument|checkState|invariant)\b!?",
            re.IGNORECASE,
        ),
    ),
    (
        "sanitize",
        "CRITICAL",
        re.compile(
            r"\b(?:sanitize\w*|escapeHtml|DOMPurify|cleanHtml|stripTags)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "allowlist",
        "CRITICAL",
        re.compile(
            r"\b(?:allowlist|allow_list|whitelist|allowedHosts|allowed_hosts|"
            r"allowedOrigins|allowed_origins|ALLOWED_HOSTS|isAllowed\w*)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "tls",
        "CRITICAL",
        re.compile(
            r"\b(?:tls|ssl|rootCertificates|rejectUnauthorized|caCert|"
            r"pinCert|minVersion|httpsOnly|strictTransport)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sandbox",
        "HIGH",
        re.compile(
            r"--sandbox\b|\b(?:rateLimit|maxBytes|bodyLimit|AbortPolicy)\b",
            re.IGNORECASE,
        ),
    ),
)


class CmdResult(NamedTuple):
    """Result from running an external subprocess command."""

    returncode: int
    stdout: str
    stderr: str


@dataclass
class DiffHunk:
    """Parsed unified diff hunk with original and new line tracking."""

    old_start: int
    new_start: int
    func_context: str
    added_lines: list[tuple[int, str]] = field(default_factory=list)
    removed_lines: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class FileDiff:
    """Parsed unified diff record for a single file."""

    path: str
    old_path: str
    status: str  # modified | added | deleted | renamed | binary
    is_binary: bool = False
    hunks: list[DiffHunk] = field(default_factory=list)


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


def has_head_commit(repo_root: Path) -> bool:
    """Return True if HEAD resolves to a valid commit."""
    res = run_cmd(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo_root)
    return res.returncode == 0


def resolve_commit(repo_root: Path, ref: str) -> str | None:
    """Verify ref resolves to a commit SHA, or return None."""
    res = run_cmd(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=repo_root,
    )
    if res.returncode != 0:
        return None
    sha = res.stdout.strip()
    return sha or None


def merge_base(repo_root: Path, ref_a: str, ref_b: str) -> str | None:
    """Return merge-base SHA of two refs, or None if unavailable."""
    res = run_cmd(["git", "merge-base", ref_a, ref_b], cwd=repo_root)
    if res.returncode != 0:
        return None
    sha = res.stdout.strip()
    return sha or None


def redact_secrets(text: str) -> str:
    """Replace likely hardcoded secret literals with a redacted marker."""

    def _repl(match: re.Match[str]) -> str:
        val = match.group(3)
        if PLACEHOLDER_SECRET_RE.search(val):
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}<REDACTED>{match.group(2)}"

    return SECRET_ASSIGN_RE.sub(_repl, text)


def fence_untrusted_snippet(raw_text: str) -> str:
    """Redact secret literals, neutralize nested fence strings, and wrap untrusted diff text."""
    cleaned = redact_secrets(raw_text)
    cleaned = cleaned.replace(FENCE_BEGIN, "[NEUTRALIZED_BEGIN_DIFF]")
    cleaned = cleaned.replace(FENCE_END, "[NEUTRALIZED_END_DIFF]")
    if len(cleaned) > MAX_SNIPPET_CHARS:
        cleaned = cleaned[: MAX_SNIPPET_CHARS - 3] + "..."
    return f"{FENCE_BEGIN}\n{cleaned}\n{FENCE_END}"


def max_tier(a: str, b: str) -> str:
    """Return the higher of two risk tiers."""
    return a if TIER_RANK[a] >= TIER_RANK[b] else b


def classify_path_tier(rel_path: str) -> tuple[str, list[str]]:
    """Assign baseline path risk tier (CRITICAL, HIGH, or STANDARD) with reasons."""
    posix = rel_path.replace("\\", "/")
    basename = Path(posix).name.lower()

    if basename in SAFE_ENV_BASENAMES:
        return "STANDARD", []
    if WORKFLOW_PATH_RE.match(posix):
        return "CRITICAL", ["ci-workflow-surface"]
    if CRITICAL_PATH_RE.search(posix):
        return "CRITICAL", ["security-sensitive-path"]
    if HIGH_PATH_RE.search(posix):
        return "HIGH", ["boundary-or-build-path"]
    return "STANDARD", []


def select_diff_command(
    repo_root: Path, base_arg: str | None
) -> tuple[list[str] | None, str, str, str | None]:
    """Choose git diff argv, diff_source label, blame_ref, or return error."""
    base_cmd = ["git", "-c", "core.quotepath=false", "diff", "--unified=3", "--no-color"]
    head_ok = has_head_commit(repo_root)

    if base_arg is not None:
        lower = base_arg.strip().lower()
        if lower == "staged":
            return base_cmd + ["--cached"], "staged", "HEAD" if head_ok else "", None
        if lower == "working":
            if head_ok:
                return base_cmd + ["HEAD"], "working-tree-vs-HEAD", "HEAD", None
            return base_cmd + ["--cached"], "staged-unborn-HEAD", "", None

        resolved = resolve_commit(repo_root, base_arg)
        if resolved is None:
            return None, "", "", f"invalid --base ref: {base_arg}"
        if head_ok:
            mb = merge_base(repo_root, resolved, "HEAD")
            target = mb or resolved
            return base_cmd + [target], f"base:{base_arg}", target, None
        return base_cmd + [resolved], f"base:{base_arg}", resolved, None

    # Auto-detect mode when --base is omitted
    if not head_ok:
        return base_cmd + ["--cached"], "staged-unborn-HEAD", "", None

    head_sha = resolve_commit(repo_root, "HEAD") or ""
    candidate_refs = ("@{u}", "origin/main", "origin/master", "main", "master")
    for ref in candidate_refs:
        ref_sha = resolve_commit(repo_root, ref)
        if ref_sha and ref_sha != head_sha:
            mb = merge_base(repo_root, ref_sha, "HEAD")
            if mb and mb != head_sha:
                return base_cmd + [mb], f"branch-vs-{ref}", mb, None

    staged_dirty = run_cmd(["git", "diff", "--cached", "--quiet"], cwd=repo_root).returncode == 1
    work_dirty = run_cmd(["git", "diff", "--quiet"], cwd=repo_root).returncode == 1

    if staged_dirty and work_dirty:
        return base_cmd + ["HEAD"], "working-tree-vs-HEAD", "HEAD", None
    if staged_dirty:
        return base_cmd + ["--cached"], "staged", "HEAD", None
    if work_dirty:
        return base_cmd, "working-tree", "HEAD", None

    return base_cmd + ["HEAD"], "clean-empty", "HEAD", None


def _strip_diff_prefix(path_token: str) -> str:
    """Strip standard git a/ or b/ prefix from diff path tokens."""
    token = path_token.strip()
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        token = token[1:-1]
    if token.startswith("a/") or token.startswith("b/"):
        return token[2:]
    return token


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """Parse git unified diff output into structured FileDiff and DiffHunk records."""
    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: DiffHunk | None = None
    old_line = 0
    new_line = 0

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            current_hunk = None
            remainder = raw_line[len("diff --git ") :]
            parts = remainder.split(" b/", 1)
            if len(parts) == 2:
                old_p = _strip_diff_prefix(parts[0])
                new_p = parts[1].strip()
            else:
                tokens = remainder.split()
                old_p = _strip_diff_prefix(tokens[0]) if tokens else ""
                new_p = _strip_diff_prefix(tokens[-1]) if tokens else old_p
            current_file = FileDiff(path=new_p, old_path=old_p, status="modified")
            files.append(current_file)
            continue

        if current_file is None:
            continue

        if raw_line.startswith("new file mode "):
            current_file.status = "added"
            continue
        if raw_line.startswith("deleted file mode "):
            current_file.status = "deleted"
            continue
        if raw_line.startswith("rename from "):
            current_file.old_path = raw_line[len("rename from ") :].strip()
            current_file.status = "renamed"
            continue
        if raw_line.startswith("rename to "):
            current_file.path = raw_line[len("rename to ") :].strip()
            current_file.status = "renamed"
            continue
        if raw_line.startswith("Binary files ") or raw_line == "GIT binary patch":
            current_file.is_binary = True
            current_file.status = "binary"
            continue
        if raw_line.startswith("--- "):
            src = raw_line[4:].strip()
            if src != "/dev/null":
                current_file.old_path = _strip_diff_prefix(src)
            continue
        if raw_line.startswith("+++ "):
            dst = raw_line[4:].strip()
            if dst == "/dev/null":
                current_file.status = "deleted"
                if current_file.old_path:
                    current_file.path = current_file.old_path
            else:
                current_file.path = _strip_diff_prefix(dst)
            continue

        hunk_match = HUNK_HEADER_RE.match(raw_line)
        if hunk_match:
            old_line = int(hunk_match.group(1))
            new_line = int(hunk_match.group(3))
            func_ctx = hunk_match.group(5).strip()
            current_hunk = DiffHunk(
                old_start=old_line,
                new_start=new_line,
                func_context=func_ctx,
            )
            current_file.hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if raw_line.startswith("\\ No newline at end of file"):
            continue
        if raw_line.startswith("+"):
            current_hunk.added_lines.append((new_line, raw_line[1:]))
            new_line += 1
        elif raw_line.startswith("-"):
            current_hunk.removed_lines.append((old_line, raw_line[1:]))
            old_line += 1
        elif raw_line.startswith(" "):
            old_line += 1
            new_line += 1

    return files


def _detect_hardcoded_secret_literal(line_text: str) -> bool:
    """Return True if an added line assigns a non-placeholder secret literal."""
    for match in SECRET_ASSIGN_RE.finditer(line_text):
        val = match.group(3)
        if not PLACEHOLDER_SECRET_RE.search(val):
            return True
    return False


def _detect_unpinned_workflow_action(rel_path: str, line_text: str) -> bool:
    """Return True if a workflow added line introduces a mutable tag-pinned action."""
    if not WORKFLOW_PATH_RE.match(rel_path.replace("\\", "/")):
        return False
    stripped = line_text.split("#", 1)[0]
    match = WORKFLOW_USES_RE.match(stripped)
    if not match:
        return False
    action_ref = match.group(1).strip()
    if action_ref.startswith("./") or action_ref.startswith("../"):
        return False
    if action_ref.startswith("docker://"):
        return "@sha256:" not in action_ref.lower()
    if "@" not in action_ref:
        return True
    _, ref = action_ref.rsplit("@", 1)
    return not SHA40_RE.match(ref)


def _is_comment_only_line(text: str) -> bool:
    """Heuristic check for pure comment lines to cut noise on removed-guard detection."""
    s = text.strip()
    return (
        not s
        or s.startswith("#")
        or s.startswith("//")
        or s.startswith("/*")
        or s.startswith("*")
        or s.startswith("<!--")
    )


def analyze_diff(
    repo_root: Path, base_arg: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    """Run git diff and classify risk tiers, bypasses, and removed security guards."""
    cmd, diff_source, blame_ref, err = select_diff_command(repo_root, base_arg)
    if err is not None or cmd is None:
        return None, err or "failed to determine diff command"

    res = run_cmd(cmd, cwd=repo_root)
    if res.returncode != 0:
        return None, res.stderr.strip() or f"git diff exited with {res.returncode}"

    parsed_files = parse_unified_diff(res.stdout)

    overall_risk = "STANDARD"
    file_reports: list[dict[str, Any]] = []
    added_signals: list[dict[str, Any]] = []
    removed_guards: list[dict[str, Any]] = []

    for fdiff in parsed_files:
        file_tier, reasons = classify_path_tier(fdiff.path)
        insertions = sum(len(h.added_lines) for h in fdiff.hunks)
        deletions = sum(len(h.removed_lines) for h in fdiff.hunks)
        symbols: list[str] = []

        for hunk in fdiff.hunks:
            if hunk.func_context and hunk.func_context not in symbols:
                symbols.append(hunk.func_context)

            added_norm_set = {
                " ".join(content.split()) for _, content in hunk.added_lines
            }

            # Scan added lines for bypasses and security hazards
            for line_no, content in hunk.added_lines:
                for rule_id, severity, autonomy, pattern in ADDED_SIGNAL_RULES:
                    if pattern.search(content):
                        file_tier = max_tier(file_tier, severity)
                        reason_tag = f"signal:{rule_id}"
                        if reason_tag not in reasons:
                            reasons.append(reason_tag)
                        added_signals.append(
                            {
                                "id": rule_id,
                                "severity": severity,
                                "autonomy": autonomy,
                                "path": fdiff.path,
                                "line": line_no,
                                "snippet_fenced": fence_untrusted_snippet(f"+{content}"),
                            }
                        )

                if _detect_hardcoded_secret_literal(content):
                    file_tier = max_tier(file_tier, "CRITICAL")
                    if "signal:hardcoded-secret-literal" not in reasons:
                        reasons.append("signal:hardcoded-secret-literal")
                    added_signals.append(
                        {
                            "id": "hardcoded-secret-literal",
                            "severity": "CRITICAL",
                            "autonomy": "ASK",
                            "path": fdiff.path,
                            "line": line_no,
                            "snippet_fenced": fence_untrusted_snippet(f"+{content}"),
                        }
                    )

                if _detect_unpinned_workflow_action(fdiff.path, content):
                    file_tier = max_tier(file_tier, "HIGH")
                    if "signal:workflow-unpinned-action" not in reasons:
                        reasons.append("signal:workflow-unpinned-action")
                    added_signals.append(
                        {
                            "id": "workflow-unpinned-action",
                            "severity": "HIGH",
                            "autonomy": "AUTO-FIX",
                            "path": fdiff.path,
                            "line": line_no,
                            "snippet_fenced": fence_untrusted_snippet(f"+{content}"),
                        }
                    )

            # Scan removed lines for deleted security / validation guards
            for old_line_no, content in hunk.removed_lines:
                if _is_comment_only_line(content):
                    continue
                norm = " ".join(content.split())
                if norm in added_norm_set:
                    # Pure re-indentation or intact move inside the same hunk
                    continue

                matched_cats: list[str] = []
                guard_sev = "HIGH"
                for cat_name, cat_sev, pattern in REMOVED_GUARD_PATTERNS:
                    if pattern.search(content):
                        matched_cats.append(cat_name)
                        guard_sev = max_tier(guard_sev, cat_sev)

                if matched_cats:
                    file_tier = max_tier(file_tier, guard_sev)
                    if "removed-security-guard" not in reasons:
                        reasons.append("removed-security-guard")
                    blame_target = blame_ref or "HEAD"
                    blame_path = fdiff.old_path or fdiff.path
                    removed_guards.append(
                        {
                            "severity": guard_sev,
                            "autonomy": "ASK",
                            "path": fdiff.path,
                            "old_line": old_line_no,
                            "categories": matched_cats,
                            "git_blame_hint": (
                                f"git blame -L {old_line_no},{old_line_no} "
                                f"{blame_target} -- {blame_path}"
                            ),
                            "snippet_fenced": fence_untrusted_snippet(f"-{content}"),
                        }
                    )

        overall_risk = max_tier(overall_risk, file_tier)
        file_reports.append(
            {
                "path": fdiff.path,
                "status": fdiff.status,
                "risk_tier": file_tier,
                "reasons": reasons,
                "insertions": insertions,
                "deletions": deletions,
                "changed_hunks": len(fdiff.hunks),
                "changed_symbols": symbols,
            }
        )

    file_reports.sort(key=lambda r: (-TIER_RANK[r["risk_tier"]], r["path"]))

    tier_counts = {"CRITICAL": 0, "HIGH": 0, "STANDARD": 0}
    for fr in file_reports:
        tier_counts[fr["risk_tier"]] += 1

    actionable = (
        overall_risk in ("CRITICAL", "HIGH")
        or bool(added_signals)
        or bool(removed_guards)
    )

    report: dict[str, Any] = {
        "status": "elevated" if actionable else "standard",
        "overall_risk": overall_risk,
        "repo_root": str(repo_root),
        "diff_source": diff_source,
        "blame_ref": blame_ref or None,
        "summary": {
            "files_changed": len(file_reports),
            "files_by_tier": tier_counts,
            "added_signals_count": len(added_signals),
            "removed_guards_count": len(removed_guards),
        },
        "files": file_reports,
        "added_signals": added_signals,
        "removed_guards": removed_guards,
    }
    return report, None


def render_text(report: dict[str, Any]) -> str:
    """Render concise human-readable diff risk triage output with fenced snippets."""
    summary = report["summary"]
    tiers = summary["files_by_tier"]
    lines: list[str] = [
        f"Pre-Push Diff Risk: {report['overall_risk']} ({report['status'].upper()})",
        f"Repository: {report['repo_root']}",
        f"Diff Source: {report['diff_source']}",
        (
            f"Changed Files: {summary['files_changed']} "
            f"(CRITICAL={tiers['CRITICAL']}, HIGH={tiers['HIGH']}, STANDARD={tiers['STANDARD']})"
        ),
        (
            f"Signals: added_hazards={summary['added_signals_count']}, "
            f"removed_guards={summary['removed_guards_count']}"
        ),
    ]

    if report["files"]:
        lines.append("")
        lines.append("Changed Files by Risk Tier:")
        for fr in report["files"]:
            reason_str = f" [{', '.join(fr['reasons'])}]" if fr["reasons"] else ""
            lines.append(
                f"  - [{fr['risk_tier']}] {fr['path']} "
                f"(+{fr['insertions']}/-{fr['deletions']}, {fr['status']}){reason_str}"
            )
            if fr["changed_symbols"]:
                lines.append(f"    Symbols: {', '.join(fr['changed_symbols'][:5])}")

    if report["added_signals"]:
        lines.append("")
        lines.append("Added Bypasses / Hazard Signals:")
        for idx, sig in enumerate(report["added_signals"], start=1):
            lines.append(
                f"  {idx}. [{sig['severity']}] [{sig['autonomy']}] {sig['id']} "
                f"at {sig['path']}:{sig['line']}"
            )
            lines.append(sig["snippet_fenced"])

    if report["removed_guards"]:
        lines.append("")
        lines.append("Removed Security / Validation Guards (run git blame):")
        for idx, guard in enumerate(report["removed_guards"], start=1):
            cats = ", ".join(guard["categories"])
            lines.append(
                f"  {idx}. [{guard['severity']}] [{guard['autonomy']}] "
                f"{guard['path']}:old-L{guard['old_line']} ({cats})"
            )
            lines.append(f"     Blame: {guard['git_blame_hint']}")
            lines.append(guard["snippet_fenced"])

    if not report["files"]:
        lines.append("")
        lines.append("No diff changes detected for the selected diff source.")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Classify git diff risk tiers, newly added bypasses/hazards, "
            "and removed security guards (read-only)."
        )
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Path inside the target git repository (default: current directory).",
    )
    parser.add_argument(
        "--base",
        default=None,
        help=(
            "Optional base ref (e.g. origin/main, HEAD~1), 'staged', or 'working'. "
            "Omit to auto-detect branch or working-tree diff."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Exit 0 = standard/low risk, 1 = elevated/signals, 2 = error."""
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

    report, err = analyze_diff(repo_root, args.base)
    if err is not None or report is None:
        print(f"error: {err or 'diff risk analysis failed'}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))

    return 1 if report["status"] == "elevated" else 0


if __name__ == "__main__":
    raise SystemExit(main())
