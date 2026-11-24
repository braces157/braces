#!/usr/bin/env python3
"""Safe file-backed PR thread replies, GraphQL resolution, and top-level PR comments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pr_context  # noqa: E402

RESOLVE_THREAD_MUTATION = """\
mutation($id: ID!) {
  resolveReviewThread(input: {threadId: $id}) {
    thread {
      id
      isResolved
    }
  }
}
"""


def _read_body_file(path_str: str) -> str:
    body_path = Path(path_str)
    if not body_path.is_file():
        raise RuntimeError(f"Body file not found: {body_path}")
    text = body_path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Body file is empty: {body_path}")
    return text


def cmd_reply_thread(
    args: argparse.Namespace, repo_root: Path, parsed_url: dict[str, Any]
) -> int:
    if not args.allow_pending:
        pending = pr_context.check_viewer_pending_review(parsed_url, repo_root)
        if pending.get("has_pending_review"):
            raise RuntimeError(
                "Viewer has an unsubmitted PENDING review on this PR. "
                "Submit or discard it on GitHub first (or pass --allow-pending)."
            )

    body = _read_body_file(args.body_file)
    endpoint = (
        f"repos/{parsed_url['owner']}/{parsed_url['repo']}/pulls/"
        f"{parsed_url['number']}/comments/{args.comment_id}/replies"
    )
    cmd = ["gh", "api"]
    if parsed_url["host"].lower() != "github.com":
        cmd.extend(["--hostname", parsed_url["host"]])
    cmd.extend([endpoint, "--method", "POST", "--input", "-"])

    res = pr_context.run_cmd(
        cmd, cwd=repo_root, stdin_text=json.dumps({"body": body})
    )
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip() or "Failed to post thread reply.")

    data = json.loads(res.stdout) if res.stdout.strip() else {}
    print(
        json.dumps(
            {
                "action": "reply-thread",
                "comment_id": args.comment_id,
                "reply_id": data.get("id"),
                "url": data.get("html_url"),
            },
            indent=2,
        )
    )
    return 0


def cmd_resolve_thread(
    args: argparse.Namespace, repo_root: Path, parsed_url: dict[str, Any]
) -> int:
    resp = pr_context.gh_api_graphql(
        RESOLVE_THREAD_MUTATION,
        {"id": args.thread_id},
        host=parsed_url["host"],
        cwd=repo_root,
    )
    thread = ((resp.get("data") or {}).get("resolveReviewThread") or {}).get("thread") or {}
    print(json.dumps({"action": "resolve-thread", "thread": thread}, indent=2))
    return 0


def cmd_reply_pr(
    args: argparse.Namespace, repo_root: Path, parsed_url: dict[str, Any]
) -> int:
    _ = _read_body_file(args.body_file)
    slug = pr_context.repo_slug_for_gh(parsed_url)
    res = pr_context.run_cmd(
        [
            "gh",
            "pr",
            "comment",
            str(parsed_url["number"]),
            "-R",
            slug,
            "--body-file",
            args.body_file,
        ],
        cwd=repo_root,
    )
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip() or "Failed to post PR comment.")
    print(
        json.dumps(
            {
                "action": "reply-pr",
                "pr": parsed_url["number"],
                "output": res.stdout.strip(),
            },
            indent=2,
        )
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Post inline thread replies, resolve GraphQL review threads, or comment on PRs."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_reply = sub.add_parser("reply-thread", help="Reply inline to a PR review comment thread.")
    p_reply.add_argument("--repo", default=".", help="Path inside the git repository.")
    p_reply.add_argument("--pr", default=None, help="PR number or URL.")
    p_reply.add_argument("--comment-id", type=int, required=True, help="Root review comment databaseId.")
    p_reply.add_argument("--body-file", required=True, help="UTF-8 file containing the reply body.")
    p_reply.add_argument(
        "--allow-pending",
        action="store_true",
        help="Allow replying even when viewer has an unsubmitted PENDING review.",
    )

    p_resolve = sub.add_parser("resolve-thread", help="Resolve an inline review thread via GraphQL.")
    p_resolve.add_argument("--repo", default=".", help="Path inside the git repository.")
    p_resolve.add_argument("--pr", default=None, help="PR number or URL.")
    p_resolve.add_argument("--thread-id", required=True, help="GraphQL node ID of the review thread.")

    p_pr = sub.add_parser("reply-pr", help="Post a top-level PR comment using --body-file.")
    p_pr.add_argument("--repo", default=".", help="Path inside the git repository.")
    p_pr.add_argument("--pr", default=None, help="PR number or URL.")
    p_pr.add_argument("--body-file", required=True, help="UTF-8 file containing the comment body.")

    return parser.parse_args()


def main() -> int:
    pr_context.configure_stdio()
    args = parse_args()
    try:
        repo_root, resolved = pr_context.resolve_repo_and_pr(
            args.repo, args.pr, fields=("number", "url", "state")
        )
        parsed_url = resolved["parsed_url"]
        handlers = {
            "reply-thread": cmd_reply_thread,
            "resolve-thread": cmd_resolve_thread,
            "reply-pr": cmd_reply_pr,
        }
        return handlers[args.command](args, repo_root, parsed_url)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
