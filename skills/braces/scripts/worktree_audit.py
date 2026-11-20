#!/usr/bin/env python3
"""Read-only git worktree and sync-drift auditor using an explicit Fact Monad."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple, Sequence

HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
ZERO_SHA = "0" * 40

REQUIRED_SAFETY_FACTS = (
    "exists_on_disk",
    "locked",
    "tracked_dirty",
    "untracked_files",
    "precious_ignored",
    "other_ignored",
    "disposable_ignored",
    "upstream_drift",
    "default_branch_drift",
    "head_merged_into_default",
    "detached_head_reachable",
    "unreachable_reflog_commits",
)

PRECIOUS_EXACT_NAMES = {
    ".env",
    ".envrc",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials.json",
    "service-account.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "secrets.toml",
    "local.properties",
    "appsettings.development.json",
    "appsettings.local.json",
    "settings.local.json",
}

PRECIOUS_PREFIXES = (
    ".env.",
    "application-local.",
    "id_rsa",
    "id_ed25519",
)

PRECIOUS_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".kdbx",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".local.json",
    ".local.toml",
    ".local.yaml",
    ".local.yml",
    ".local.env",
)

PRECIOUS_DIR_PREFIXES = (
    ".claude",
    ".codex",
    ".gemini",
)

PRECIOUS_NAME_SUBSTRINGS = (
    "secret",
    "credential",
)

DISPOSABLE_DIRS = {
    "node_modules",
    "target",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".astro",
    ".vite",
    ".turbo",
    ".parcel-cache",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    ".gradle",
    "dist",
    "build",
    "out",
    "coverage",
    ".nyc_output",
    "htmlcov",
    "playwright-report",
    "test-results",
}

DISPOSABLE_FILES = {
    ".eslintcache",
    ".stylelintcache",
    ".ds_store",
    "thumbs.db",
}

DISPOSABLE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".pyd",
    ".class",
    ".o",
    ".obj",
    ".tsbuildinfo",
)

STATUS_CLEAN_ACTIVE = "CLEAN_ACTIVE"
STATUS_SAFE_TO_PRUNE = "SAFE_TO_PRUNE"
STATUS_NEEDS_SYNC = "NEEDS_SYNC"
STATUS_DIRTY_HOLD = "DIRTY_HOLD"
STATUS_UNKNOWN = "UNKNOWN"


class CmdResult(NamedTuple):
    """Result from executing a subprocess without shell expansion."""

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
    """Execute a read-only external command via argv list with shell=False."""
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd is not None else None,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return CmdResult(proc.returncode, proc.stdout, proc.stderr)
    except OSError as exc:
        return CmdResult(127, "", str(exc))


def _norm_path_key(path: Path) -> str:
    """Return a canonical, case-normalized filesystem key safe on Windows and POSIX."""
    return os.path.normcase(os.path.realpath(str(path)))


def known_fact(value: Any) -> dict[str, Any]:
    """Construct a resolved Fact Monad entry."""
    return {"known": True, "value": value}


def unknown_fact(reason: str) -> dict[str, Any]:
    """Construct an unresolved Fact Monad entry that blocks unsafe pruning."""
    return {"known": False, "value": None, "reason": reason}


def is_known(fact: dict[str, Any] | None) -> bool:
    """Return True only when fact is a dict explicitly marked known=True."""
    return isinstance(fact, dict) and fact.get("known") is True


def find_repo_root(start: Path) -> Path | None:
    """Resolve the enclosing git repository root from start path."""
    target = start if start.is_dir() else start.parent
    res = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=target)
    if res.returncode != 0:
        return None
    root = res.stdout.strip()
    return Path(root) if root else None


def parse_worktree_porcelain(raw: str) -> list[dict[str, Any]]:
    """Parse `git worktree list --porcelain` blocks into structured records."""
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def _finish() -> None:
        nonlocal current
        if current is not None:
            records.append(current)
            current = None

    for raw_line in raw.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            _finish()
            continue
        if line.startswith("worktree "):
            _finish()
            current = {
                "path": line[len("worktree ") :],
                "head_sha": None,
                "branch": None,
                "branch_ref": None,
                "detached": False,
                "bare": False,
                "locked": False,
                "lock_reason": None,
                "prunable": False,
                "prunable_reason": None,
            }
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["head_sha"] = line[len("HEAD ") :].strip()
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            current["branch_ref"] = ref
            if ref.startswith("refs/heads/"):
                current["branch"] = ref[len("refs/heads/") :]
            else:
                current["branch"] = ref
        elif line == "detached":
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
        elif line == "locked":
            current["locked"] = True
        elif line.startswith("locked "):
            current["locked"] = True
            current["lock_reason"] = line[len("locked ") :].strip() or None
        elif line == "prunable":
            current["prunable"] = True
        elif line.startswith("prunable "):
            current["prunable"] = True
            current["prunable_reason"] = line[len("prunable ") :].strip() or None

    _finish()
    return records


def parse_status_porcelain_z(raw: str) -> tuple[list[str], list[str], list[str]]:
    """Parse `git status --porcelain=v1 -z --ignored=matching` output."""
    tracked_dirty: list[str] = []
    untracked: list[str] = []
    ignored: list[str] = []

    tokens = raw.split("\0")
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 4:
            continue
        x, y = entry[0], entry[1]
        path = entry[3:].replace("\\", "/")
        if x in ("R", "C") or y in ("R", "C"):
            if i < len(tokens):
                i += 1
        if x == "!" and y == "!":
            ignored.append(path)
        elif x == "?" and y == "?":
            untracked.append(path)
        else:
            tracked_dirty.append(path)

    return sorted(set(tracked_dirty)), sorted(set(untracked)), sorted(set(ignored))


def classify_ignored_path(rel_path: str) -> str:
    """Classify an ignored path as 'precious', 'disposable', or 'other'."""
    posix = rel_path.replace("\\", "/").strip("/")
    parts = [p.lower() for p in posix.split("/") if p]
    if not parts:
        return "other"

    name = parts[-1]
    lower_posix = "/".join(parts)

    if (
        name in PRECIOUS_EXACT_NAMES
        or name.startswith(PRECIOUS_PREFIXES)
        or name.endswith(PRECIOUS_SUFFIXES)
        or any(sub in name for sub in PRECIOUS_NAME_SUBSTRINGS)
        or any(part in PRECIOUS_DIR_PREFIXES for part in parts)
        or lower_posix == ".vscode/settings.json"
        or lower_posix.endswith("/.vscode/settings.json")
    ):
        return "precious"

    if (
        any(part in DISPOSABLE_DIRS for part in parts)
        or name in DISPOSABLE_FILES
        or name.endswith(DISPOSABLE_SUFFIXES)
    ):
        return "disposable"

    return "other"


def _ref_exists(repo_root: Path, full_ref: str) -> bool:
    res = run_cmd(["git", "show-ref", "--verify", "--quiet", full_ref], cwd=repo_root)
    return res.returncode == 0


def detect_default_branch(repo_root: Path) -> tuple[str | None, str | None]:
    """Detect default branch name and preferred comparison ref (origin/<branch> or local)."""
    sym_res = run_cmd(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo_root,
    )
    if sym_res.returncode == 0:
        ref = sym_res.stdout.strip()
        if ref.startswith("origin/"):
            branch = ref[len("origin/") :]
            if _ref_exists(repo_root, f"refs/remotes/origin/{branch}"):
                return branch, f"origin/{branch}"
            if _ref_exists(repo_root, f"refs/heads/{branch}"):
                return branch, branch
        elif ref and _ref_exists(repo_root, f"refs/heads/{ref}"):
            return ref, ref

    for candidate in ("main", "master"):
        if _ref_exists(repo_root, f"refs/remotes/origin/{candidate}"):
            return candidate, f"origin/{candidate}"
        if _ref_exists(repo_root, f"refs/heads/{candidate}"):
            return candidate, candidate

    return None, None


def _rev_list_left_right(repo_root: Path, left_ref: str, right_ref: str) -> tuple[int, int] | None:
    """Return (left_only/behind, right_only/ahead) counts for `left_ref...right_ref`."""
    res = run_cmd(
        ["git", "rev-list", "--left-right", "--count", f"{left_ref}...{right_ref}"],
        cwd=repo_root,
    )
    if res.returncode != 0:
        return None
    parts = res.stdout.strip().split()
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    return int(parts[0]), int(parts[1])


def audit_default_branch_sync(repo_root: Path, default_branch: str | None) -> dict[str, Any]:
    """Audit whether local default branch lags behind or diverges from origin/<default>."""
    if not default_branch:
        return unknown_fact("could not determine default branch (no origin/HEAD, main, or master)")

    local_ref = f"refs/heads/{default_branch}"
    remote_ref = f"refs/remotes/origin/{default_branch}"
    has_local = _ref_exists(repo_root, local_ref)
    has_remote = _ref_exists(repo_root, remote_ref)

    if not has_local and not has_remote:
        return unknown_fact(f"default branch refs missing for {default_branch}")

    if not has_remote:
        return known_fact(
            {
                "default_branch": default_branch,
                "local_ref": local_ref if has_local else None,
                "remote_ref": None,
                "has_origin": False,
                "ahead": 0,
                "behind": 0,
                "behind_origin": False,
                "diverged": False,
            }
        )

    if not has_local:
        return known_fact(
            {
                "default_branch": default_branch,
                "local_ref": None,
                "remote_ref": remote_ref,
                "has_origin": True,
                "ahead": 0,
                "behind": 0,
                "behind_origin": False,
                "diverged": False,
            }
        )

    counts = _rev_list_left_right(repo_root, remote_ref, local_ref)
    if counts is None:
        return unknown_fact(f"git rev-list failed comparing {remote_ref}...{local_ref}")

    behind, ahead = counts
    return known_fact(
        {
            "default_branch": default_branch,
            "local_ref": local_ref,
            "remote_ref": remote_ref,
            "has_origin": True,
            "ahead": ahead,
            "behind": behind,
            "behind_origin": behind > 0,
            "diverged": ahead > 0 and behind > 0,
        }
    )


def _check_detached_and_reflog(
    wt_path: Path,
    head_sha: str | None,
    detached: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Evaluate detached-HEAD reachability and unreachable commits in worktree HEAD reflog."""
    if not head_sha or head_sha == ZERO_SHA or not HEX40_RE.match(head_sha):
        reason = "unborn or invalid HEAD SHA"
        return unknown_fact(reason), unknown_fact(reason)

    if detached:
        ref_res = run_cmd(
            [
                "git",
                "for-each-ref",
                "--contains",
                head_sha,
                "--format=%(refname)",
                "refs/heads",
                "refs/remotes",
                "refs/tags",
            ],
            cwd=wt_path,
        )
        if ref_res.returncode != 0:
            err = ref_res.stderr.strip() or "for-each-ref --contains failed"
            detached_fact = unknown_fact(f"detached reachability check failed: {err}")
        else:
            containing_refs = [
                line.strip() for line in ref_res.stdout.splitlines() if line.strip()
            ]
            detached_fact = known_fact(bool(containing_refs))
    else:
        detached_fact = known_fact(True)

    reflog_res = run_cmd(
        ["git", "log", "-g", "--format=%H%x00%gs", "-n", "20", "HEAD"],
        cwd=wt_path,
    )
    if reflog_res.returncode != 0:
        err = reflog_res.stderr.strip() or "git log -g failed"
        return detached_fact, unknown_fact(f"reflog probe failed: {err}")

    candidate_shas: list[str] = []
    seen: set[str] = set()
    if detached:
        candidate_shas.append(head_sha)
        seen.add(head_sha)

    for line in reflog_res.stdout.splitlines():
        if not line:
            continue
        parts = line.split("\0", 1)
        sha = parts[0].strip()
        subject = parts[1].strip().lower() if len(parts) > 1 else ""
        if not HEX40_RE.match(sha) or sha == ZERO_SHA:
            continue
        if not detached and (
            subject.startswith("commit (amend):")
            or subject.startswith("rebase")
            or subject.startswith("pull")
        ):
            continue
        if sha not in seen:
            seen.add(sha)
            candidate_shas.append(sha)

    if not candidate_shas:
        return detached_fact, known_fact([])

    reach_res = run_cmd(
        [
            "git",
            "rev-list",
            "--max-count=50",
            *candidate_shas,
            "--not",
            "--branches",
            "--remotes",
            "--tags",
        ],
        cwd=wt_path,
    )
    if reach_res.returncode != 0:
        err = reach_res.stderr.strip() or "git rev-list reachability failed"
        return detached_fact, unknown_fact(f"reflog reachability failed: {err}")

    unreachable = [s.strip() for s in reach_res.stdout.splitlines() if s.strip()]
    return detached_fact, known_fact(unreachable)


def collect_worktree_facts(
    wt: dict[str, Any],
    repo_root: Path,
    default_branch: str | None,
    default_compare_ref: str | None,
) -> dict[str, Any]:
    """Collect every Fact Monad entry for a single worktree strictly read-only."""
    wt_path = Path(wt["path"])
    exists = wt_path.is_dir()
    detached = bool(wt["detached"])
    head_sha = wt.get("head_sha")
    branch = wt.get("branch")

    facts: dict[str, Any] = {
        "exists_on_disk": known_fact(exists),
        "locked": known_fact(bool(wt["locked"])),
    }

    if not exists or wt.get("prunable"):
        missing_reason = (
            f"worktree directory missing or prunable ({wt.get('prunable_reason') or 'path not found'})"
        )
        facts["tracked_dirty"] = unknown_fact(missing_reason)
        facts["untracked_files"] = unknown_fact(missing_reason)
        facts["precious_ignored"] = unknown_fact(missing_reason)
        facts["other_ignored"] = unknown_fact(missing_reason)
        facts["disposable_ignored"] = unknown_fact(missing_reason)
        facts["upstream_drift"] = unknown_fact(missing_reason)
        facts["default_branch_drift"] = unknown_fact(missing_reason)
        facts["head_merged_into_default"] = unknown_fact(missing_reason)
        facts["detached_head_reachable"] = unknown_fact(missing_reason)
        facts["unreachable_reflog_commits"] = unknown_fact(missing_reason)
        return facts

    status_res = run_cmd(
        ["git", "status", "--porcelain=v1", "-z", "--ignored=matching"],
        cwd=wt_path,
    )
    if status_res.returncode != 0:
        err = status_res.stderr.strip() or "git status failed"
        reason = f"git status failed: {err}"
        facts["tracked_dirty"] = unknown_fact(reason)
        facts["untracked_files"] = unknown_fact(reason)
        facts["precious_ignored"] = unknown_fact(reason)
        facts["other_ignored"] = unknown_fact(reason)
        facts["disposable_ignored"] = unknown_fact(reason)
    else:
        tracked, untracked, ignored = parse_status_porcelain_z(status_res.stdout)
        precious: list[str] = []
        disposable: list[str] = []
        other: list[str] = []
        for path in ignored:
            kind = classify_ignored_path(path)
            if kind == "precious":
                precious.append(path)
            elif kind == "disposable":
                disposable.append(path)
            else:
                other.append(path)
        facts["tracked_dirty"] = known_fact(tracked)
        facts["untracked_files"] = known_fact(untracked)
        facts["precious_ignored"] = known_fact(precious)
        facts["other_ignored"] = known_fact(other)
        facts["disposable_ignored"] = known_fact(disposable)

    # Upstream tracking drift
    if detached or not branch:
        facts["upstream_drift"] = known_fact(
            {"upstream": None, "ahead": None, "behind": None}
        )
    else:
        up_res = run_cmd(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=wt_path,
        )
        if up_res.returncode != 0:
            stderr_low = up_res.stderr.lower()
            if (
                "no upstream" in stderr_low
                or "does not point to a branch" in stderr_low
                or "no such branch" in stderr_low
            ):
                facts["upstream_drift"] = known_fact(
                    {"upstream": None, "ahead": None, "behind": None}
                )
            else:
                facts["upstream_drift"] = unknown_fact(
                    f"upstream probe failed: {up_res.stderr.strip() or 'unknown error'}"
                )
        else:
            upstream_ref = up_res.stdout.strip()
            counts = _rev_list_left_right(wt_path, upstream_ref, "HEAD")
            if counts is None:
                facts["upstream_drift"] = unknown_fact(
                    f"git rev-list failed for upstream {upstream_ref}"
                )
            else:
                behind, ahead = counts
                facts["upstream_drift"] = known_fact(
                    {"upstream": upstream_ref, "ahead": ahead, "behind": behind}
                )

    # Default branch drift & merge ancestry
    if not default_branch or not default_compare_ref:
        reason = "default branch could not be determined"
        facts["default_branch_drift"] = unknown_fact(reason)
        facts["head_merged_into_default"] = unknown_fact(reason)
    elif not head_sha or head_sha == ZERO_SHA or not HEX40_RE.match(head_sha):
        reason = "unborn or invalid HEAD SHA"
        facts["default_branch_drift"] = unknown_fact(reason)
        facts["head_merged_into_default"] = unknown_fact(reason)
    else:
        counts = _rev_list_left_right(repo_root, default_compare_ref, head_sha)
        if counts is None:
            facts["default_branch_drift"] = unknown_fact(
                f"git rev-list failed comparing {default_compare_ref}...{head_sha}"
            )
        else:
            behind_def, ahead_def = counts
            facts["default_branch_drift"] = known_fact(
                {"ref": default_compare_ref, "ahead": ahead_def, "behind": behind_def}
            )

        anc_res = run_cmd(
            ["git", "merge-base", "--is-ancestor", head_sha, default_compare_ref],
            cwd=repo_root,
        )
        if anc_res.returncode == 0:
            facts["head_merged_into_default"] = known_fact(True)
        elif anc_res.returncode == 1:
            facts["head_merged_into_default"] = known_fact(False)
        else:
            facts["head_merged_into_default"] = unknown_fact(
                f"git merge-base --is-ancestor failed: {anc_res.stderr.strip()}"
            )

    detached_fact, reflog_fact = _check_detached_and_reflog(wt_path, head_sha, detached)
    facts["detached_head_reachable"] = detached_fact
    facts["unreachable_reflog_commits"] = reflog_fact

    return facts


def classify_worktree(
    facts: dict[str, Any],
    *,
    is_primary: bool,
    is_default_branch: bool,
    detached: bool,
    lock_reason: str | None = None,
) -> tuple[str, list[str], str]:
    """Classify a worktree strictly via the Fact Monad.

    Any missing or `known: false` safety fact automatically downgrades the
    worktree into `UNKNOWN`, making unsafe pruning structurally impossible.
    """
    unknown_reasons: list[str] = []
    for key in REQUIRED_SAFETY_FACTS:
        fact = facts.get(key)
        if not is_known(fact):
            detail = (
                fact.get("reason", "fact unavailable")
                if isinstance(fact, dict)
                else "fact missing"
            )
            unknown_reasons.append(f"unknown {key}: {detail}")

    if unknown_reasons:
        return (
            STATUS_UNKNOWN,
            unknown_reasons,
            "inspect unknown fact(s) before modifying or pruning",
        )

    hold_reasons: list[str] = []
    if facts["locked"]["value"]:
        suffix = f" ({lock_reason})" if lock_reason else ""
        hold_reasons.append(f"worktree is locked{suffix}")

    tracked = facts["tracked_dirty"]["value"]
    if tracked:
        hold_reasons.append(f"{len(tracked)} tracked modified/staged path(s)")

    untracked = facts["untracked_files"]["value"]
    if untracked:
        hold_reasons.append(f"{len(untracked)} untracked path(s)")

    precious = facts["precious_ignored"]["value"]
    if precious:
        hold_reasons.append(
            f"{len(precious)} precious ignored path(s): {', '.join(precious[:3])}"
        )

    other_ignored = facts["other_ignored"]["value"]
    if other_ignored:
        hold_reasons.append(
            f"{len(other_ignored)} unclassified ignored path(s): {', '.join(other_ignored[:3])}"
        )

    if detached and not facts["detached_head_reachable"]["value"]:
        hold_reasons.append("detached HEAD commit is unreachable from any branch or tag")

    unreachable_reflog = facts["unreachable_reflog_commits"]["value"]
    if unreachable_reflog:
        hold_reasons.append(
            f"{len(unreachable_reflog)} unreachable commit(s) in worktree reflog"
        )

    if hold_reasons:
        return (
            STATUS_DIRTY_HOLD,
            hold_reasons,
            "hold: rescue, commit, stash, or explicitly review before any removal",
        )

    sync_reasons: list[str] = []
    up = facts["upstream_drift"]["value"]
    if up["upstream"]:
        if up["ahead"]:
            sync_reasons.append(f"{up['ahead']} commit(s) ahead of {up['upstream']}")
        if up["behind"]:
            sync_reasons.append(f"{up['behind']} commit(s) behind {up['upstream']}")

    def_drift = facts["default_branch_drift"]["value"]
    merged_into_default = bool(facts["head_merged_into_default"]["value"])

    if is_default_branch:
        if def_drift["behind"]:
            sync_reasons.append(
                f"local default branch is {def_drift['behind']} commit(s) behind {def_drift['ref']}"
            )
        if def_drift["ahead"]:
            sync_reasons.append(
                f"local default branch is {def_drift['ahead']} commit(s) ahead of {def_drift['ref']}"
            )
    elif not merged_into_default:
        if not up["upstream"]:
            sync_reasons.append(
                f"{def_drift['ahead']} unmerged commit(s) ahead of {def_drift['ref']} with no upstream"
            )
        if def_drift["behind"]:
            sync_reasons.append(
                f"unmerged branch is {def_drift['behind']} commit(s) behind {def_drift['ref']}"
            )

    if sync_reasons:
        action = (
            "fast-forward clean default branch after confirmation (git pull --ff-only)"
            if is_default_branch
            else "report drift and hand off branch push/PR update to pr-workflow"
        )
        return STATUS_NEEDS_SYNC, sync_reasons, action

    if not is_primary and not is_default_branch and merged_into_default:
        disposable = facts["disposable_ignored"]["value"]
        reasons = [f"all commits merged into {def_drift['ref']} and working tree clean"]
        if disposable:
            reasons.append(
                f"only disposable cache(s) present: {', '.join(disposable[:3])}"
            )
        return (
            STATUS_SAFE_TO_PRUNE,
            reasons,
            "safe to prune after user confirmation (git worktree remove)",
        )

    return STATUS_CLEAN_ACTIVE, [], "keep (clean and synced)"


def audit_repository(repo_path: Path) -> dict[str, Any]:
    """Run a complete read-only worktree and sync-drift audit on a git repository."""
    if not repo_path.exists():
        raise ValueError(f"Repository path does not exist: {repo_path}")

    repo_root = find_repo_root(repo_path)
    if repo_root is None:
        raise ValueError(f"Not inside a git repository: {repo_path}")

    wt_res = run_cmd(["git", "worktree", "list", "--porcelain"], cwd=repo_root)
    if wt_res.returncode != 0:
        raise RuntimeError(
            f"git worktree list failed: {wt_res.stderr.strip() or 'unknown error'}"
        )

    raw_worktrees = parse_worktree_porcelain(wt_res.stdout)
    if not raw_worktrees:
        raise RuntimeError("git worktree list returned no worktrees")

    default_branch, default_compare_ref = detect_default_branch(repo_root)
    default_branch_sync = audit_default_branch_sync(repo_root, default_branch)

    current_key = _norm_path_key(repo_root)
    main_repo_root = raw_worktrees[0]["path"]

    audited_worktrees: list[dict[str, Any]] = []
    counts = {
        STATUS_CLEAN_ACTIVE: 0,
        STATUS_SAFE_TO_PRUNE: 0,
        STATUS_NEEDS_SYNC: 0,
        STATUS_DIRTY_HOLD: 0,
        STATUS_UNKNOWN: 0,
    }

    for idx, wt in enumerate(raw_worktrees):
        is_primary = idx == 0
        wt_path = Path(wt["path"])
        is_current = _norm_path_key(wt_path) == current_key
        is_default_branch = bool(
            default_branch and wt.get("branch") and wt.get("branch") == default_branch
        )

        if wt.get("bare"):
            status = STATUS_CLEAN_ACTIVE
            reasons = ["bare repository container"]
            recommended_action = "keep (bare repository)"
            facts: dict[str, Any] = {
                "exists_on_disk": known_fact(wt_path.is_dir()),
                "locked": known_fact(bool(wt["locked"])),
            }
        else:
            facts = collect_worktree_facts(
                wt, repo_root, default_branch, default_compare_ref
            )
            status, reasons, recommended_action = classify_worktree(
                facts,
                is_primary=is_primary,
                is_default_branch=is_default_branch,
                detached=bool(wt["detached"]),
                lock_reason=wt.get("lock_reason"),
            )

        counts[status] += 1
        audited_worktrees.append(
            {
                "path": str(wt_path),
                "is_primary": is_primary,
                "is_current": is_current,
                "bare": bool(wt.get("bare")),
                "head_sha": wt.get("head_sha"),
                "branch": wt.get("branch"),
                "detached": bool(wt.get("detached")),
                "locked": bool(wt.get("locked")),
                "lock_reason": wt.get("lock_reason"),
                "prunable": bool(wt.get("prunable")),
                "status": status,
                "reasons": reasons,
                "recommended_action": recommended_action,
                "facts": facts,
            }
        )

    default_sync_ok = is_known(default_branch_sync) and not bool(
        default_branch_sync["value"].get("behind_origin")
    ) and not bool(default_branch_sync["value"].get("ahead"))

    all_clean_and_synced = (
        default_sync_ok
        and counts[STATUS_SAFE_TO_PRUNE] == 0
        and counts[STATUS_NEEDS_SYNC] == 0
        and counts[STATUS_DIRTY_HOLD] == 0
        and counts[STATUS_UNKNOWN] == 0
    )

    return {
        "repo_root": str(repo_root),
        "main_repo_root": str(main_repo_root),
        "default_branch": default_branch,
        "default_compare_ref": default_compare_ref,
        "default_branch_sync": default_branch_sync,
        "summary": {
            "total": len(audited_worktrees),
            **counts,
            "all_clean_and_synced": all_clean_and_synced,
        },
        "worktrees": audited_worktrees,
    }


def render_text(report: dict[str, Any]) -> str:
    """Render a compact human-readable audit summary."""
    lines: list[str] = []
    lines.append(f"Repository: {report['repo_root']}")
    lines.append(
        f"Default branch: {report['default_branch'] or 'UNKNOWN'} "
        f"(compare ref: {report['default_compare_ref'] or 'UNKNOWN'})"
    )

    dbs = report["default_branch_sync"]
    if is_known(dbs):
        val = dbs["value"]
        if val.get("has_origin"):
            lines.append(
                f"Default sync vs origin: ahead={val['ahead']}, behind={val['behind']}"
            )
        else:
            lines.append("Default sync vs origin: no origin tracking ref")
    else:
        lines.append(f"Default sync vs origin: UNKNOWN ({dbs.get('reason', '')})")

    summary = report["summary"]
    lines.append(
        "Summary: "
        f"total={summary['total']} "
        f"CLEAN_ACTIVE={summary[STATUS_CLEAN_ACTIVE]} "
        f"SAFE_TO_PRUNE={summary[STATUS_SAFE_TO_PRUNE]} "
        f"NEEDS_SYNC={summary[STATUS_NEEDS_SYNC]} "
        f"DIRTY_HOLD={summary[STATUS_DIRTY_HOLD]} "
        f"UNKNOWN={summary[STATUS_UNKNOWN]}"
    )
    lines.append("")

    for wt in report["worktrees"]:
        label = wt["branch"] or ("DETACHED" if wt["detached"] else "BARE")
        role = "primary" if wt["is_primary"] else "linked"
        lines.append(f"[{wt['status']}] ({role}) {wt['path']}  [{label}]")
        for reason in wt["reasons"]:
            lines.append(f"  - {reason}")
        lines.append(f"  -> {wt['recommended_action']}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Strictly read-only git worktree and sync-drift auditor "
            "using an explicit Fact Monad."
        )
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
    configure_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return 0 if code == 0 else 2

    try:
        report = audit_repository(Path(args.repo))
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))

    return 0 if report["summary"]["all_clean_and_synced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
