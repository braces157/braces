#!/usr/bin/env python3
"""Fetch paginated PR comments, review bodies, and review threads via GraphQL."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pr_context  # noqa: E402

FEEDBACK_QUERY = """\
query(
  $owner: String!,
  $repo: String!,
  $number: Int!,
  $commentsCursor: String,
  $reviewsCursor: String,
  $threadsCursor: String
) {
  viewer {
    login
  }
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      number
      url
      title
      state
      headRefOid
      comments(first: 100, after: $commentsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          databaseId
          url
          body
          createdAt
          updatedAt
          author { login __typename }
        }
      }
      reviews(first: 100, after: $reviewsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          databaseId
          url
          state
          body
          submittedAt
          author { login __typename }
        }
      }
      reviewThreads(first: 100, after: $threadsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          diffSide
          startLine
          originalLine
          resolvedBy { login }
          comments(first: 100) {
            nodes {
              id
              databaseId
              url
              body
              createdAt
              updatedAt
              author { login __typename }
            }
          }
        }
      }
    }
  }
}
"""

KNOWN_BOT_PREFIXES = (
    "coderabbitai",
    "copilot",
    "cursor",
    "github-actions",
    "vercel",
    "netlify",
    "codecov",
    "sonarcloud",
    "dependabot",
    "renovate",
    "greptile",
    "sourcery",
    "qodo",
    "ellipsis",
)

WRAPPER_PATTERNS = (
    re.compile(r"the latest updates on your projects", re.I),
    re.compile(r"deploy preview for .* ready", re.I),
    re.compile(r"<!--\s*walkthrough_start\s*-->", re.I),
    re.compile(r"<!--\s*coverage-monitor-action", re.I),
    re.compile(r"##\s*codecov\s+report", re.I),
    re.compile(r"sonarcloud quality gate.*passed", re.I),
)


def is_bot_author(author: dict[str, Any] | None) -> bool:
    """Determine if an author node represents a GitHub App or review bot."""
    if not author:
        return False
    typename = str(author.get("__typename") or "")
    login = str(author.get("login") or "").lower()
    if typename == "Bot" or login.endswith("[bot]"):
        return True
    base_login = login.removesuffix("[bot]")
    return any(base_login == prefix or base_login.startswith(prefix) for prefix in KNOWN_BOT_PREFIXES)


def is_wrapper_comment(author: dict[str, Any] | None, body: str) -> bool:
    """Identify pure bot summary/deployment wrapper text without concrete findings."""
    if not is_bot_author(author):
        return False
    text = (body or "").strip()
    if not text:
        return True

    # If CodeRabbit reports >0 actionable comments inside a review body, keep it.
    actionable = re.search(r"actionable comments posted:\s*(\d+)", text, re.I)
    if actionable and int(actionable.group(1)) > 0:
        return False

    if "<!-- walkthrough_start -->" in text.lower():
        if not actionable or int(actionable.group(1)) == 0:
            return True

    return any(pat.search(text) for pat in WRAPPER_PATTERNS)


def fetch_all_feedback(
    parsed_url: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    """Fetch and paginate all PR comments, reviews, and inline review threads."""
    comments: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    threads: list[dict[str, Any]] = []

    comments_cursor: str | None = None
    reviews_cursor: str | None = None
    threads_cursor: str | None = None
    need_comments = True
    need_reviews = True
    need_threads = True

    pr_meta: dict[str, Any] = {}
    viewer_login = ""

    while need_comments or need_reviews or need_threads:
        resp = pr_context.gh_api_graphql(
            FEEDBACK_QUERY,
            {
                "owner": parsed_url["owner"],
                "repo": parsed_url["repo"],
                "number": parsed_url["number"],
                "commentsCursor": comments_cursor,
                "reviewsCursor": reviews_cursor,
                "threadsCursor": threads_cursor,
            },
            host=parsed_url["host"],
            cwd=repo_root,
        )
        data = resp.get("data") or {}
        if not viewer_login:
            viewer_login = ((data.get("viewer") or {}).get("login") or "").strip()

        pr = (data.get("repository") or {}).get("pullRequest") or {}
        if not pr:
            raise RuntimeError("Pull request not found via GraphQL.")

        if not pr_meta:
            pr_meta = {
                "number": pr.get("number"),
                "url": pr.get("url"),
                "title": pr.get("title"),
                "state": pr.get("state"),
                "headRefOid": pr.get("headRefOid"),
            }

        if need_comments:
            conn = pr.get("comments") or {}
            comments.extend([n for n in (conn.get("nodes") or []) if n])
            page = conn.get("pageInfo") or {}
            need_comments = bool(page.get("hasNextPage"))
            comments_cursor = page.get("endCursor")

        if need_reviews:
            conn = pr.get("reviews") or {}
            reviews.extend([n for n in (conn.get("nodes") or []) if n])
            page = conn.get("pageInfo") or {}
            need_reviews = bool(page.get("hasNextPage"))
            reviews_cursor = page.get("endCursor")

        if need_threads:
            conn = pr.get("reviewThreads") or {}
            threads.extend([n for n in (conn.get("nodes") or []) if n])
            page = conn.get("pageInfo") or {}
            need_threads = bool(page.get("hasNextPage"))
            threads_cursor = page.get("endCursor")

    return {
        "viewer": viewer_login or None,
        "pr": pr_meta,
        "comments": comments,
        "reviews": reviews,
        "review_threads": threads,
    }


def _dedupe_by_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        if item_id and item_id in seen:
            continue
        if item_id:
            seen.add(item_id)
        out.append(item)
    return out


def structure_feedback(
    raw: dict[str, Any],
    include_resolved: bool = False,
    include_outdated: bool = False,
) -> dict[str, Any]:
    """Classify threads, reviews, comments, pending viewer drafts, and wrappers."""
    viewer = (raw.get("viewer") or "").lower()
    comments = _dedupe_by_id(raw["comments"])
    reviews = _dedupe_by_id(raw["reviews"])
    threads = _dedupe_by_id(raw["review_threads"])

    pending_reviews = []
    actionable_reviews = []
    filtered_wrappers = []

    for rev in reviews:
        author = rev.get("author") or {}
        login = (author.get("login") or "").lower()
        if rev.get("state") == "PENDING" and (not viewer or login == viewer):
            pending_reviews.append(rev)
            continue
        body = (rev.get("body") or "").strip()
        if not body:
            continue
        item = {
            "kind": "review",
            "id": rev.get("id"),
            "databaseId": rev.get("databaseId"),
            "url": rev.get("url"),
            "state": rev.get("state"),
            "author": author.get("login"),
            "is_bot": is_bot_author(author),
            "submittedAt": rev.get("submittedAt"),
            "body": body,
        }
        if is_wrapper_comment(author, body):
            item["is_wrapper"] = True
            filtered_wrappers.append(item)
        else:
            actionable_reviews.append(item)

    actionable_comments = []
    for com in comments:
        author = com.get("author") or {}
        body = (com.get("body") or "").strip()
        item = {
            "kind": "issue_comment",
            "id": com.get("id"),
            "databaseId": com.get("databaseId"),
            "url": com.get("url"),
            "author": author.get("login"),
            "is_bot": is_bot_author(author),
            "createdAt": com.get("createdAt"),
            "body": body,
        }
        if is_wrapper_comment(author, body):
            item["is_wrapper"] = True
            filtered_wrappers.append(item)
        else:
            actionable_comments.append(item)

    resolved_count = sum(1 for t in threads if t.get("isResolved"))
    outdated_unresolved_count = sum(
        1 for t in threads if not t.get("isResolved") and t.get("isOutdated")
    )

    selected_threads = []
    for thr in threads:
        is_resolved = bool(thr.get("isResolved"))
        is_outdated = bool(thr.get("isOutdated"))
        if is_resolved and not include_resolved:
            continue
        if not is_resolved and is_outdated and not include_outdated:
            continue

        t_comments = [c for c in ((thr.get("comments") or {}).get("nodes") or []) if c]
        root = t_comments[0] if t_comments else {}
        selected_threads.append(
            {
                "thread_id": thr.get("id"),
                "root_comment_id": root.get("databaseId"),
                "isResolved": is_resolved,
                "isOutdated": is_outdated,
                "path": thr.get("path"),
                "line": thr.get("line") or thr.get("originalLine"),
                "diffSide": thr.get("diffSide"),
                "comments": [
                    {
                        "id": c.get("id"),
                        "databaseId": c.get("databaseId"),
                        "url": c.get("url"),
                        "author": (c.get("author") or {}).get("login"),
                        "is_bot": is_bot_author(c.get("author")),
                        "createdAt": c.get("createdAt"),
                        "body": c.get("body") or "",
                    }
                    for c in t_comments
                ],
            }
        )

    return {
        "viewer": raw.get("viewer"),
        "pr": raw["pr"],
        "has_viewer_pending_review": bool(pending_reviews),
        "viewer_pending_reviews": pending_reviews,
        "counts": {
            "total_threads": len(threads),
            "resolved_threads": resolved_count,
            "outdated_unresolved_threads": outdated_unresolved_count,
            "open_threads_returned": len(selected_threads),
            "actionable_reviews": len(actionable_reviews),
            "actionable_issue_comments": len(actionable_comments),
            "filtered_wrappers": len(filtered_wrappers),
        },
        "threads": selected_threads,
        "reviews": actionable_reviews,
        "issue_comments": actionable_comments,
        "filtered_wrappers": filtered_wrappers,
    }


def render_text(structured: dict[str, Any]) -> str:
    """Format PR feedback for human/agent inspection with untrusted boundaries."""
    pr = structured["pr"]
    counts = structured["counts"]
    lines = [
        f"PR #{pr.get('number')}: {pr.get('title')} ({pr.get('url')})",
        f"Head SHA: {pr.get('headRefOid')} | Resolved threads: {counts['resolved_threads']} | "
        f"Outdated unresolved omitted: {counts['outdated_unresolved_threads']} | "
        f"Bot wrappers filtered: {counts['filtered_wrappers']}",
    ]

    if structured["has_viewer_pending_review"]:
        lines.append(
            "\nWARNING: Viewer has an unsubmitted PENDING review on this PR. "
            "Submit or discard it on GitHub before posting inline replies."
        )

    if structured["threads"]:
        lines.append("\n=== Inline Review Threads ===")
        for idx, thr in enumerate(structured["threads"], start=1):
            loc = f"{thr.get('path')}:{thr.get('line') or '?'}"
            flags = []
            if thr.get("isResolved"):
                flags.append("RESOLVED")
            if thr.get("isOutdated"):
                flags.append("OUTDATED")
            flag_str = f" [{' '.join(flags)}]" if flags else ""
            lines.append(
                f"[{idx}] {loc}{flag_str} (thread_id={thr.get('thread_id')}, root_comment_id={thr.get('root_comment_id')})"
            )
            lines.append("--- BEGIN UNTRUSTED REVIEW CONTENT ---")
            for c in thr["comments"]:
                tag = " [bot]" if c["is_bot"] else ""
                lines.append(f"@{c['author'] or 'unknown'}{tag}: {c['body'].strip()}")
            lines.append("--- END UNTRUSTED REVIEW CONTENT ---")

    if structured["reviews"]:
        lines.append("\n=== Review Bodies ===")
        for idx, rev in enumerate(structured["reviews"], start=1):
            tag = " [bot]" if rev["is_bot"] else ""
            lines.append(f"[R{idx}] @{rev['author'] or 'unknown'}{tag} ({rev['state']}):")
            lines.append("--- BEGIN UNTRUSTED REVIEW CONTENT ---")
            lines.append(rev["body"])
            lines.append("--- END UNTRUSTED REVIEW CONTENT ---")

    if structured["issue_comments"]:
        lines.append("\n=== Top-Level Issue Comments ===")
        for idx, com in enumerate(structured["issue_comments"], start=1):
            tag = " [bot]" if com["is_bot"] else ""
            lines.append(f"[C{idx}] @{com['author'] or 'unknown'}{tag}:")
            lines.append("--- BEGIN UNTRUSTED REVIEW CONTENT ---")
            lines.append(com["body"])
            lines.append("--- END UNTRUSTED REVIEW CONTENT ---")

    if not (structured["threads"] or structured["reviews"] or structured["issue_comments"]):
        lines.append("\nNo open actionable PR feedback found.")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch PR review threads, review bodies, and issue comments."
    )
    parser.add_argument("--repo", default=".", help="Path inside the git repository.")
    parser.add_argument("--pr", default=None, help="PR number or URL (default: current branch PR).")
    parser.add_argument(
        "--include-resolved",
        action="store_true",
        help="Include already resolved review threads.",
    )
    parser.add_argument(
        "--include-outdated",
        action="store_true",
        help="Include outdated unresolved review threads.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser.parse_args()


def main() -> int:
    pr_context.configure_stdio()
    args = parse_args()
    try:
        repo_root, resolved = pr_context.resolve_repo_and_pr(args.repo, args.pr)
        raw = fetch_all_feedback(resolved["parsed_url"], repo_root)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    structured = structure_feedback(
        raw,
        include_resolved=args.include_resolved,
        include_outdated=args.include_outdated,
    )
    if args.json:
        print(json.dumps(structured, indent=2, ensure_ascii=False))
    else:
        print(render_text(structured))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
