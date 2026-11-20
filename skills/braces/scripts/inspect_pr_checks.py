#!/usr/bin/env python3
"""Inspect GitHub PR checks, extract GitHub Actions failure snippets, and report external checks."""

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

ACTIONS_URL_RE = re.compile(r"/actions/runs/(\d+)(?:/job/(\d+))?")

CHECK_FIELDS = [
    "name",
    "state",
    "bucket",
    "link",
    "startedAt",
    "completedAt",
    "workflow",
]

RUN_VIEW_FIELDS = [
    "name",
    "workflowName",
    "conclusion",
    "status",
    "url",
    "event",
    "headBranch",
    "headSha",
]

FAILURE_STATES = {"failure", "error", "cancelled", "timed_out", "action_required", "startup_failure"}
PENDING_STATES = {"pending", "queued", "in_progress", "waiting", "requested", "expected"}
SKIPPED_STATES = {"skipped", "neutral", "stale"}

FAILURE_MARKERS = (
    "error",
    "fail",
    "failed",
    "traceback",
    "exception",
    "assert",
    "panic",
    "fatal",
    "timeout",
    "exit code",
)
PENDING_LOG_MARKERS = (
    "still in progress",
    "log will be available when it is complete",
)

DEFAULT_MAX_LINES = 160
DEFAULT_CONTEXT_LINES = 30


def parse_actions_url(url: str | None) -> tuple[str, str | None] | None:
    """Extract (run_id, optional job_id) from a GitHub Actions URL, else None."""
    if not url:
        return None
    match = ACTIONS_URL_RE.search(url)
    if not match:
        return None
    return match.group(1), match.group(2)


def classify_check(check: dict[str, Any]) -> str:
    """Classify a gh pr checks entry into failing, pending, skipped, or passing."""
    bucket = str(check.get("bucket") or "").strip().lower()
    state = str(check.get("state") or check.get("conclusion") or "").strip().lower()

    if bucket == "fail" or state in FAILURE_STATES:
        return "failing"
    if bucket == "pending" or state in PENDING_STATES:
        return "pending"
    if bucket in ("skipping", "cancel") or state in SKIPPED_STATES:
        return "skipped"
    return "passing"


def extract_failure_snippet(
    log_text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    context_lines: int = DEFAULT_CONTEXT_LINES,
) -> str:
    """Extract focused lines around failure markers, capped at max_lines."""
    lines = log_text.splitlines()
    if not lines:
        return ""

    hit_indices = [
        idx
        for idx, line in enumerate(lines)
        if any(marker in line.lower() for marker in FAILURE_MARKERS)
    ]

    if not hit_indices:
        return "\n".join(lines[-max_lines:])

    selected: list[int] = []
    seen: set[int] = set()
    # Focus around the last few error blocks where root failure usually prints.
    for center in reversed(hit_indices):
        start = max(0, center - context_lines)
        end = min(len(lines), center + context_lines + 1)
        for i in range(start, end):
            if i not in seen:
                seen.add(i)
                selected.append(i)
        if len(selected) >= max_lines:
            break

    selected_sorted = sorted(selected)[-max_lines:]
    return "\n".join(lines[i] for i in selected_sorted)


def _fetch_run_log(
    run_id: str,
    job_id: str | None,
    parsed_url: dict[str, Any],
    repo_root: Path,
) -> tuple[str | None, str | None]:
    slug = pr_context.repo_slug_for_gh(parsed_url)
    host = parsed_url["host"]

    for flag in ("--log-failed", "--log"):
        res = pr_context.run_cmd(
            ["gh", "run", "view", run_id, "-R", slug, flag],
            cwd=repo_root,
        )
        combined = (res.stdout or "") + "\n" + (res.stderr or "")
        if res.returncode == 0 and res.stdout.strip():
            if not any(m in combined.lower() for m in PENDING_LOG_MARKERS):
                return res.stdout, None

    if job_id:
        cmd = ["gh", "api"]
        if host.lower() != "github.com":
            cmd.extend(["--hostname", host])
        cmd.append(f"/repos/{parsed_url['owner']}/{parsed_url['repo']}/actions/jobs/{job_id}/logs")
        job_res = pr_context.run_cmd(cmd, cwd=repo_root)
        if job_res.returncode == 0 and job_res.stdout.strip():
            return job_res.stdout, None
        return None, (job_res.stderr or "Job log unavailable").strip()

    return None, "Run logs unavailable or still in progress."


def analyze_failing_check(
    check: dict[str, Any],
    parsed_url: dict[str, Any],
    repo_root: Path,
    max_lines: int,
    context_lines: int,
) -> dict[str, Any]:
    """Analyze one failing check: Actions run metadata + snippet or external URL only."""
    details_url = str(check.get("link") or check.get("detailsUrl") or "")
    actions_ids = parse_actions_url(details_url)
    if actions_ids is None:
        return {
            "name": check.get("name"),
            "state": check.get("state"),
            "provider": "external",
            "details_url": details_url,
            "note": "External CI provider; log retrieval is out of scope.",
        }

    run_id, job_id = actions_ids
    slug = pr_context.repo_slug_for_gh(parsed_url)
    run_meta, run_err = pr_context.run_gh_json_with_field_fallback(
        ["gh", "run", "view", run_id, "-R", slug],
        RUN_VIEW_FIELDS,
        cwd=repo_root,
    )
    log_text, log_err = _fetch_run_log(run_id, job_id, parsed_url, repo_root)
    snippet = (
        extract_failure_snippet(log_text, max_lines=max_lines, context_lines=context_lines)
        if log_text
        else None
    )

    return {
        "name": check.get("name"),
        "state": check.get("state"),
        "workflow": check.get("workflow"),
        "provider": "github-actions",
        "details_url": details_url,
        "run_id": run_id,
        "job_id": job_id,
        "run_metadata": run_meta,
        "run_metadata_error": run_err,
        "log_snippet": snippet,
        "log_error": log_err,
    }


def render_text(report: dict[str, Any], show_all: bool = False) -> str:
    """Format PR checks report as concise text."""
    pr = report["pr"]
    counts = report["counts"]
    lines = [
        f"PR #{pr.get('number')} ({pr.get('url')}) | head={pr.get('headRefOid')}",
        f"Checks: failing={counts['failing']}, pending={counts['pending']}, "
        f"passing={counts['passing']}, skipped={counts['skipped']}",
    ]

    if not report["results"]:
        lines.append("No failing checks detected.")
    else:
        for res in report["results"]:
            lines.append(f"\n--- FAIL: {res.get('name')} ({res.get('provider')}) ---")
            lines.append(f"URL: {res.get('details_url')}")
            if res["provider"] == "external":
                lines.append(res["note"])
                continue
            if res.get("log_snippet"):
                lines.append("--- BEGIN UNTRUSTED CI LOG SNIPPET ---")
                lines.append(res["log_snippet"])
                lines.append("--- END UNTRUSTED CI LOG SNIPPET ---")
            else:
                lines.append(f"Log unavailable: {res.get('log_error')}")

    if show_all and report.get("other_checks"):
        lines.append("\n=== Non-Failing Checks ===")
        for chk in report["other_checks"]:
            lines.append(f"- [{chk['category'].upper()}] {chk.get('name')} ({chk.get('state')})")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect PR checks and extract GitHub Actions failure snippets."
    )
    parser.add_argument("--repo", default=".", help="Path inside the git repository.")
    parser.add_argument("--pr", default=None, help="PR number or URL (default: current branch PR).")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    parser.add_argument("--context", type=int, default=DEFAULT_CONTEXT_LINES)
    parser.add_argument("--all", action="store_true", help="Include non-failing checks in output.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser.parse_args()


def main() -> int:
    pr_context.configure_stdio()
    args = parse_args()
    try:
        repo_root, resolved = pr_context.resolve_repo_and_pr(args.repo, args.pr)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    parsed_url = resolved["parsed_url"]
    slug = pr_context.repo_slug_for_gh(parsed_url)
    checks, err = pr_context.run_gh_json_with_field_fallback(
        ["gh", "pr", "checks", str(parsed_url["number"]), "-R", slug],
        CHECK_FIELDS,
        cwd=repo_root,
    )
    if err is not None or not isinstance(checks, list):
        print(f"Error fetching PR checks: {err}", file=sys.stderr)
        return 2

    counts = {"failing": 0, "pending": 0, "passing": 0, "skipped": 0}
    failing_checks = []
    other_checks = []
    for chk in checks:
        cat = classify_check(chk)
        counts[cat] += 1
        if cat == "failing":
            failing_checks.append(chk)
        else:
            other_checks.append({**chk, "category": cat})

    results = [
        analyze_failing_check(
            chk,
            parsed_url=parsed_url,
            repo_root=repo_root,
            max_lines=max(1, args.max_lines),
            context_lines=max(1, args.context),
        )
        for chk in failing_checks
    ]

    report: dict[str, Any] = {
        "pr": resolved["pr"],
        "counts": counts,
        "results": results,
    }
    if args.all:
        report["other_checks"] = other_checks

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report, show_all=args.all))

    return 1 if failing_checks else 0


if __name__ == "__main__":
    raise SystemExit(main())
