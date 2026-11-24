#!/usr/bin/env python3
"""Collect git, worktree, PR template, and GitHub PR context for PR workflows."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple, Sequence

PR_URL_RE = re.compile(
    r"^https?://([^/]+)/([^/]+)/([^/]+)/pull/(\d+)(?:[/?#].*)?$"
)

SUSPECT_EXACT_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
}
SUSPECT_PREFIXES = (".env.", "id_rsa.", "id_ed25519.")
SUSPECT_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".log")
SUSPECT_SUBSTRINGS = ("secret", "credential")
SUSPECT_DIRS = ("node_modules", "dist", "build", ".venv", "__pycache__")

DEFAULT_PR_FIELDS = [
    "number",
    "url",
    "title",
    "state",
    "isDraft",
    "baseRefName",
    "headRefName",
    "headRefOid",
    "mergeable",
    "mergeStateStatus",
    "reviewDecision",
]

PENDING_REVIEW_QUERY = """\
query($owner: String!, $repo: String!, $number: Int!) {
  viewer {
    login
  }
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviews(states: [PENDING], first: 20) {
        nodes {
          id
          databaseId
          state
          author {
            login
          }
        }
      }
    }
  }
}
"""


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


def run_cmd(
    cmd: Sequence[str],
    cwd: Path | None = None,
    stdin_text: str | None = None,
) -> CmdResult:
    """Execute a command via argv list without shell expansion."""
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd is not None else None,
            input=stdin_text,
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


def parse_pr_url(url: str) -> dict[str, Any] | None:
    """Parse canonical GitHub or GHE PR URL into host, owner, repo, and number."""
    if not url:
        return None
    match = PR_URL_RE.match(url.strip())
    if not match:
        return None
    host, owner, repo, number_str = match.groups()
    return {
        "host": host,
        "owner": owner,
        "repo": repo,
        "number": int(number_str),
    }


def repo_slug_for_gh(parsed_url: dict[str, Any]) -> str:
    """Build the -R target understood by gh CLI for both github.com and GHE."""
    host = parsed_url["host"]
    owner = parsed_url["owner"]
    repo = parsed_url["repo"]
    if host.lower() == "github.com":
        return f"{owner}/{repo}"
    return f"{host}/{owner}/{repo}"


def _norm_path_key(path: Path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def detect_git_context(repo_root: Path) -> dict[str, Any]:
    """Inspect current branch, HEAD SHA, detached state, and linked worktree info."""
    branch_res = run_cmd(["git", "branch", "--show-current"], cwd=repo_root)
    current_branch = branch_res.stdout.strip() if branch_res.returncode == 0 else ""

    head_res = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_root)
    head_sha = head_res.stdout.strip() if head_res.returncode == 0 else None

    git_dir_res = run_cmd(["git", "rev-parse", "--git-dir"], cwd=repo_root)
    git_common_res = run_cmd(["git", "rev-parse", "--git-common-dir"], cwd=repo_root)

    git_dir = Path(git_dir_res.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (repo_root / git_dir).resolve()
    else:
        git_dir = git_dir.resolve()

    git_common_dir = Path(git_common_res.stdout.strip())
    if not git_common_dir.is_absolute():
        git_common_dir = (repo_root / git_common_dir).resolve()
    else:
        git_common_dir = git_common_dir.resolve()

    is_worktree = _norm_path_key(git_dir) != _norm_path_key(git_common_dir)

    main_repo_root = repo_root
    if is_worktree:
        candidate = git_common_dir.parent
        top_res = run_cmd(["git", "-C", str(candidate), "rev-parse", "--show-toplevel"])
        if top_res.returncode == 0 and top_res.stdout.strip():
            main_repo_root = Path(top_res.stdout.strip())
        else:
            main_repo_root = candidate

    return {
        "repo_root": str(repo_root),
        "current_branch": current_branch or None,
        "head_sha": head_sha,
        "is_detached_head": not bool(current_branch),
        "is_worktree": is_worktree,
        "git_dir": str(git_dir),
        "git_common_dir": str(git_common_dir),
        "main_repo_root": str(main_repo_root),
    }


def detect_default_branch(repo_root: Path, gh_available: bool = True) -> str | None:
    """Detect repository default branch from origin/HEAD, gh metadata, or local refs."""
    sym_res = run_cmd(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo_root,
    )
    if sym_res.returncode == 0:
        ref = sym_res.stdout.strip()
        if ref.startswith("origin/"):
            return ref[len("origin/") :]
        if ref:
            return ref

    if gh_available:
        gh_res = run_cmd(
            ["gh", "repo", "view", "--json", "defaultBranchRef"],
            cwd=repo_root,
        )
        if gh_res.returncode == 0 and gh_res.stdout.strip():
            try:
                data = json.loads(gh_res.stdout)
                name = (data.get("defaultBranchRef") or {}).get("name")
                if name:
                    return str(name)
            except json.JSONDecodeError:
                pass

    for candidate in ("main", "master"):
        for ref in (f"refs/remotes/origin/{candidate}", f"refs/heads/{candidate}"):
            check = run_cmd(["git", "show-ref", "--verify", "--quiet", ref], cwd=repo_root)
            if check.returncode == 0:
                return candidate
    return None


def is_suspect_path(rel_path: str) -> bool:
    """Return True if a path looks like a secret, log, or generated build artifact."""
    posix = rel_path.replace("\\", "/").strip("/")
    parts = [p.lower() for p in posix.split("/") if p]
    if not parts:
        return False
    name = parts[-1]
    if any(part in SUSPECT_DIRS for part in parts[:-1]):
        return True
    if name in SUSPECT_DIRS:
        return True
    if name in SUSPECT_EXACT_NAMES or name.startswith(SUSPECT_PREFIXES):
        return True
    if name.endswith(SUSPECT_SUFFIXES):
        return True
    if any(sub in name for sub in SUSPECT_SUBSTRINGS):
        return True
    return False


def _parse_porcelain_z(raw: str) -> tuple[list[str], list[str], list[str]]:
    staged: list[str] = []
    modified: list[str] = []
    untracked: list[str] = []

    tokens = raw.split("\0")
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 4:
            continue
        x, y = entry[0], entry[1]
        path = entry[3:].replace("\\", "/")
        if x in ("R", "C") and i < len(tokens):
            # Porcelain v1 -z emits the original path as an extra NUL token.
            i += 1
        if x == "?" and y == "?":
            untracked.append(path)
            continue
        if x not in (" ", "?"):
            staged.append(path)
        if y not in (" ", "?"):
            modified.append(path)

    return staged, modified, untracked


def collect_working_state(
    repo_root: Path,
    default_branch: str | None,
    current_branch: str | None,
) -> dict[str, Any]:
    """Collect working tree changes, suspect files, upstream tracking, and ahead count."""
    status_res = run_cmd(["git", "status", "--porcelain=v1", "-z"], cwd=repo_root)
    staged, modified, untracked = _parse_porcelain_z(status_res.stdout)

    all_paths = sorted(set(staged + modified + untracked))
    suspect_files = [p for p in all_paths if is_suspect_path(p)]

    upstream_res = run_cmd(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=repo_root,
    )
    upstream = upstream_res.stdout.strip() if upstream_res.returncode == 0 else None

    ahead_default = None
    if default_branch:
        base_ref = default_branch
        origin_ref = f"origin/{default_branch}"
        if (
            run_cmd(
                ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{origin_ref}"],
                cwd=repo_root,
            ).returncode
            == 0
        ):
            base_ref = origin_ref
        cnt_res = run_cmd(
            ["git", "rev-list", "--count", f"{base_ref}..HEAD"],
            cwd=repo_root,
        )
        if cnt_res.returncode == 0 and cnt_res.stdout.strip().isdigit():
            ahead_default = int(cnt_res.stdout.strip())

    ahead_upstream = None
    if upstream:
        up_cnt = run_cmd(
            ["git", "rev-list", "--count", f"{upstream}..HEAD"],
            cwd=repo_root,
        )
        if up_cnt.returncode == 0 and up_cnt.stdout.strip().isdigit():
            ahead_upstream = int(up_cnt.stdout.strip())

    return {
        "dirty": bool(staged or modified or untracked),
        "staged_files": staged,
        "modified_files": modified,
        "untracked_files": untracked,
        "suspect_files": suspect_files,
        "upstream": upstream,
        "commits_ahead_of_default": ahead_default,
        "commits_ahead_of_upstream": ahead_upstream,
        "on_default_branch": bool(
            default_branch and current_branch and current_branch == default_branch
        ),
    }


def discover_pr_templates(repo_root: Path) -> dict[str, Any]:
    """Discover GitHub PR templates with case-insensitive deduplication for Windows."""
    seen_keys: set[str] = set()
    found: list[str] = []

    single_candidates = [
        ".github/pull_request_template.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        "pull_request_template.md",
        "PULL_REQUEST_TEMPLATE.md",
        "docs/pull_request_template.md",
        "docs/PULL_REQUEST_TEMPLATE.md",
    ]
    dir_candidates = [
        ".github/pull_request_template",
        ".github/PULL_REQUEST_TEMPLATE",
    ]

    def _add_file(path: Path) -> None:
        if not path.is_file():
            return
        key = _norm_path_key(path)
        if key in seen_keys:
            return
        seen_keys.add(key)
        found.append(path.relative_to(repo_root).as_posix())

    for rel in single_candidates:
        _add_file(repo_root / rel)

    for rel_dir in dir_candidates:
        dpath = repo_root / rel_dir
        if dpath.is_dir():
            for child in sorted(dpath.iterdir(), key=lambda p: p.name.lower()):
                if child.is_file() and child.suffix.lower() == ".md":
                    _add_file(child)

    status = "none"
    if len(found) == 1:
        status = "single"
    elif len(found) > 1:
        status = "multiple"

    return {"status": status, "templates": found}


def check_gh_auth(repo_root: Path) -> dict[str, Any]:
    """Check whether GitHub CLI is installed and authenticated."""
    if shutil.which("gh") is None:
        return {
            "installed": False,
            "authenticated": False,
            "message": "gh CLI not found in PATH; install GitHub CLI and run `gh auth login`.",
        }
    res = run_cmd(["gh", "auth", "status"], cwd=repo_root)
    msg = (res.stdout or res.stderr).strip()
    return {
        "installed": True,
        "authenticated": res.returncode == 0,
        "message": msg,
    }


def _extract_unknown_json_fields(stderr: str) -> list[str]:
    return re.findall(r'Unknown JSON field:\s*"([^"]+)"', stderr)


def run_gh_json_with_field_fallback(
    base_cmd: Sequence[str],
    fields: Sequence[str],
    cwd: Path,
) -> tuple[Any | None, str | None]:
    """Run a gh command with --json fields, dropping fields rejected by older gh builds."""
    active_fields = list(fields)
    while active_fields:
        cmd = [*base_cmd, "--json", ",".join(active_fields)]
        res = run_cmd(cmd, cwd=cwd)
        if res.returncode == 0:
            try:
                return json.loads(res.stdout), None
            except json.JSONDecodeError as exc:
                return None, f"Invalid JSON from gh: {exc}"
        unknown = _extract_unknown_json_fields(res.stderr)
        if unknown:
            remaining = [f for f in active_fields if f not in set(unknown)]
            if len(remaining) < len(active_fields):
                active_fields = remaining
                continue
        return None, (res.stderr or res.stdout).strip()
    return None, "All JSON fields rejected by gh CLI."


def resolve_pr(
    pr_arg: str | None,
    repo_root: Path,
    fields: Sequence[str] = DEFAULT_PR_FIELDS,
) -> dict[str, Any]:
    """Resolve current branch PR or explicit PR number/URL, distinguishing none vs error."""
    base_cmd = ["gh", "pr", "view"]
    if pr_arg:
        base_cmd.append(str(pr_arg))

    data, err = run_gh_json_with_field_fallback(base_cmd, fields, cwd=repo_root)
    if err is None and isinstance(data, dict):
        parsed = parse_pr_url(str(data.get("url", "")))
        return {
            "status": "found",
            "pr": data,
            "parsed_url": parsed,
            "error": None,
        }

    err_lower = (err or "").lower()
    no_pr_markers = (
        "no pull requests found",
        "no open pull requests found",
        "could not find a pull request",
    )
    if any(marker in err_lower for marker in no_pr_markers):
        return {
            "status": "none",
            "pr": None,
            "parsed_url": None,
            "error": None,
        }

    return {
        "status": "error",
        "pr": None,
        "parsed_url": None,
        "error": err or "Unknown error resolving PR.",
    }


def resolve_repo_and_pr(
    repo_arg: str,
    pr_arg: str | None,
    fields: Sequence[str] = ("number", "url", "title", "state", "headRefOid"),
) -> tuple[Path, dict[str, Any]]:
    """Validate git repo, gh auth, and target PR, raising RuntimeError on failure."""
    repo_root = find_repo_root(Path(repo_arg))
    if repo_root is None:
        raise RuntimeError(f"not inside a Git repository: {repo_arg}")

    gh_auth = check_gh_auth(repo_root)
    if not gh_auth["installed"] or not gh_auth["authenticated"]:
        raise RuntimeError(gh_auth["message"])

    resolved = resolve_pr(pr_arg, repo_root, fields=fields)
    if resolved["status"] != "found" or not resolved["parsed_url"]:
        raise RuntimeError(resolved["error"] or "No pull request found for current branch.")

    return repo_root, resolved


def gh_api_graphql(
    query: str,
    variables: dict[str, Any],
    host: str | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Execute a GraphQL query/mutation safely via gh api graphql --input -."""
    cmd = ["gh", "api", "graphql"]
    if host and host.lower() != "github.com":
        cmd.extend(["--hostname", host])
    cmd.extend(["--input", "-"])
    payload = json.dumps({"query": query, "variables": variables})
    res = run_cmd(cmd, cwd=cwd, stdin_text=payload)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip() or "gh api graphql failed")
    try:
        parsed = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse GraphQL JSON: {exc}") from exc
    if parsed.get("errors"):
        raise RuntimeError(f"GraphQL errors: {json.dumps(parsed['errors'])}")
    return parsed


def check_viewer_pending_review(
    parsed_url: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    """Check whether the authenticated viewer has an unsubmitted PENDING review."""
    try:
        resp = gh_api_graphql(
            PENDING_REVIEW_QUERY,
            {
                "owner": parsed_url["owner"],
                "repo": parsed_url["repo"],
                "number": parsed_url["number"],
            },
            host=parsed_url["host"],
            cwd=repo_root,
        )
    except RuntimeError as exc:
        return {"checked": False, "has_pending_review": False, "error": str(exc)}

    data = resp.get("data") or {}
    viewer_login = ((data.get("viewer") or {}).get("login") or "").lower()
    reviews = (
        ((data.get("repository") or {}).get("pullRequest") or {}).get("reviews") or {}
    ).get("nodes") or []

    pending_ids = []
    for rev in reviews:
        if not rev:
            continue
        author = ((rev.get("author") or {}).get("login") or "").lower()
        if rev.get("state") == "PENDING" and (not viewer_login or author == viewer_login):
            pending_ids.append(rev.get("databaseId") or rev.get("id"))

    return {
        "checked": True,
        "has_pending_review": bool(pending_ids),
        "pending_review_ids": pending_ids,
        "viewer": viewer_login or None,
    }


def gather_context(repo_path: Path, pr_arg: str | None = None) -> dict[str, Any]:
    """Collect full repository and PR context dictionary."""
    repo_root = find_repo_root(repo_path)
    if repo_root is None:
        return {"error": f"Not inside a Git repository: {repo_path}"}

    gh_auth = check_gh_auth(repo_root)
    git_ctx = detect_git_context(repo_root)
    default_branch = detect_default_branch(
        repo_root, gh_available=bool(gh_auth["installed"] and gh_auth["authenticated"])
    )
    working_state = collect_working_state(
        repo_root, default_branch, git_ctx["current_branch"]
    )
    templates = discover_pr_templates(repo_root)

    if gh_auth["installed"] and gh_auth["authenticated"]:
        existing_pr = resolve_pr(pr_arg, repo_root)
    else:
        existing_pr = {
            "status": "error",
            "pr": None,
            "parsed_url": None,
            "error": gh_auth["message"],
        }

    viewer_pending = {"checked": False, "has_pending_review": False}
    if existing_pr["status"] == "found" and existing_pr["parsed_url"]:
        viewer_pending = check_viewer_pending_review(
            existing_pr["parsed_url"], repo_root
        )

    return {
        "git": git_ctx,
        "default_branch": default_branch,
        "working_state": working_state,
        "templates": templates,
        "gh_auth": gh_auth,
        "existing_pr": existing_pr,
        "viewer_pending_review": viewer_pending,
    }


def render_text(ctx: dict[str, Any]) -> str:
    """Format context dictionary into a concise dashboard."""
    if "error" in ctx:
        return f"ERROR: {ctx['error']}"

    git = ctx["git"]
    ws = ctx["working_state"]
    tpl = ctx["templates"]
    pr_info = ctx["existing_pr"]
    gh_auth = ctx["gh_auth"]

    lines = [
        f"Repo root:       {git['repo_root']}",
        f"Branch:          {git['current_branch'] or '(DETACHED HEAD)'} @ {(git['head_sha'] or 'unborn')[:12]}",
        f"Default branch:  {ctx['default_branch'] or '(unknown)'}",
        f"Worktree:        {'yes (main: ' + git['main_repo_root'] + ')' if git['is_worktree'] else 'no'}",
        f"Working tree:    {'dirty' if ws['dirty'] else 'clean'} "
        f"(staged={len(ws['staged_files'])}, modified={len(ws['modified_files'])}, untracked={len(ws['untracked_files'])})",
    ]
    if ws["suspect_files"]:
        lines.append(f"Suspect files:   {', '.join(ws['suspect_files'])}")
    lines.append(
        f"PR templates:    {tpl['status']}"
        + (f" ({', '.join(tpl['templates'])})" if tpl["templates"] else "")
    )
    lines.append(
        f"gh auth:         {'ok' if gh_auth['authenticated'] else 'NOT AUTHENTICATED'}"
    )
    if pr_info["status"] == "found":
        pr = pr_info["pr"]
        lines.append(
            f"PR:              #{pr.get('number')} [{pr.get('state')}{' DRAFT' if pr.get('isDraft') else ''}] {pr.get('url')}"
        )
        lines.append(
            f"Merge status:    mergeable={pr.get('mergeable')} mergeState={pr.get('mergeStateStatus')} reviewDecision={pr.get('reviewDecision')}"
        )
        if ctx["viewer_pending_review"].get("has_pending_review"):
            lines.append(
                "WARNING:         Viewer has an unsubmitted PENDING review on this PR!"
            )
    elif pr_info["status"] == "none":
        lines.append("PR:              none open for current branch")
    else:
        lines.append(f"PR:              error ({pr_info['error']})")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect git, worktree, template, and GitHub PR preflight context."
    )
    parser.add_argument("--repo", default=".", help="Path inside the git repository.")
    parser.add_argument("--pr", default=None, help="Optional PR number or URL.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    ctx = gather_context(Path(args.repo), args.pr)
    if "error" in ctx:
        if args.json:
            print(json.dumps(ctx, indent=2, ensure_ascii=False))
        print(f"error: {ctx['error']}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(ctx, indent=2, ensure_ascii=False))
    else:
        print(render_text(ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
