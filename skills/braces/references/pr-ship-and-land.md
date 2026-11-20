# PR Shipping, Prose Standards & Landing Playbook

Use this guide when authoring commit messages or PR descriptions (`describe`), shipping branches into GitHub PRs (`ship`), or merging approved PRs with branch/worktree teardown (`land`). For review feedback, remote CI repair, or shepherding an open PR, read [pr-feedback-and-ci.md](pr-feedback-and-ci.md).

## 1. Preflight & Non-Negotiable Safety Gates

1. Verify `gh auth status`. If `gh` is missing or unauthenticated, stop and instruct the user to run `gh auth login`.
2. Run preflight before mutating git or GitHub state:
   ```bash
   python "<skill-dir>/scripts/pr_context.py" --repo "." [--pr "<ref>"] --json
   ```
   Inspect `git` (`current_branch`, `is_detached_head`, `is_worktree`, `main_repo_root`), `default_branch`, `working_state` (`suspect_files`, `commits_ahead_of_default`), `existing_pr` (`"found"` | `"none"` | `"error"`), and `templates` (`"none"` | `"single"` | `"multiple"`). If `existing_pr.status == "error"`, stop — never risk opening a duplicate PR.
3. **Hard Guardrails:**
   - Never push to `main`/`master`; re-check `git branch --show-current` right before every push.
   - Never run `git add -A` or `git add .`; stage explicit named files only and exclude `suspect_files` (`.env*`, keys, artifacts).
   - Never pass `--no-verify`, `--no-gpg-sign`, `--force`, or auto-merge without explicit approval (use `--force-with-lease` only when a rebase push is authorized).

## 2. Commit & PR Prose Standards (Diff-Grounded)

Write for a reviewer who knows the repo but has not seen this chat — reason and observable result before implementation detail.

- **Ground in Actual Diffs:** Base commits on the staged diff; base PR text on the full branch range (excluding lockfiles/generated code):
  ```bash
  git log --oneline <base_branch>..HEAD
  git diff --stat <base_branch>...HEAD
  git diff <base_branch>...HEAD -- . ':!*lock*' ':!*.lock' ':!*generated*'
  ```
  Verify old descriptions/plans against the real diff; never invent issue IDs, causes, or test results.
- **Titles & Commit Subjects:** Match repo history (`git log -n 10 --oneline`) or `type(scope): imperative subject` (`feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `chore`, `ci`; prefer `fix:` when ambiguous). Stay `<= 72` chars, sentence case, plain verb, no trailing period. Describe the combined outcome in the PR title; individual commits describe their own staged diff.
- **PR Templates & Existing Bodies:**
  - `templates.status == "single"`: preserve headings/checklists; mark `[x]` **only** on actually verified items.
  - `templates.status == "multiple"`: pick an unambiguous match (noting your choice) or ask the user.
  - Updating an existing PR: run `gh pr view <pr> --json title,body` first; **never delete** user screenshots, images, or notes unless instructed.
- **Fallback Body (Scaled to Blast Radius):**
  - *Small fix:* 2–4 crisp sentences covering **Why**, **What Changed**, and **Verification**.
  - *Substantive change:* `## Why` (problem/motivation), `## What Changed` (net change with repo-relative backticked paths, 0–2 compact file/call/Mermaid views if helpful; omit dead ends, agent rosters, and raw disk paths), `## Verification` / `## Evidence` (exact commands + observed `passed`/`failed`/`pending`/`UNPERFORMED`), and `## Merge Danger` (**Door:** two-way vs one-way; **Blast Radius:** Low/Medium/High).
  - Use closing keywords (`Fixes #123`) **only** when targeting the default branch and completely resolving the issue (otherwise `Refs #123`). Never reference the PR's own number.
- **Safe Multiline Delivery:** Always write multiline commit/PR bodies as UTF-8 temp files with real newlines and pass `--body-file "<tmp>"` (or `git commit -F "<tmp>"`), then delete `<tmp>` immediately. In `describe`-only mode, print the title/body in chat without git/gh mutations.

## 3. Shipping Workflow (`ship`)

1. **Branch Routing:** Detached `HEAD` or default branch with uncommitted changes -> `git checkout -b <type>/<short-slug>`. Default branch with unpushed commits -> ask before carrying onto a feature branch. Clean default branch -> report "no feature work" and stop.
2. **Pre-Landing Gate:** Run `review` / `verify` checks in [review-and-verify.md](review-and-verify.md) on non-trivial diffs and execute the repo's local test/lint/build suite. **Hard Gate:** never push or open a ready PR on failing checks (only `--draft` if the user explicitly requests WIP with acknowledged failures).
3. **Bisectable Commits & Push:** Stage explicit files (1 commit if `<50` lines across `<4` files; otherwise 2–3 dependency-ordered commits: schema/infra -> core + tests -> API/UI/docs). Re-verify `git branch --show-current`, then `git push -u origin HEAD`.
4. **Create or Update PR:** Re-run `scripts/pr_context.py`. If open PR exists, preserve `isDraft` and user images (preview updates before `gh pr edit --body-file` unless immediate edit requested). If none exists, run `gh pr create --base "<base>" --title "<title>" --body-file "<tmp>" [--draft]`. Keep linked worktrees intact for follow-up review.

## 4. Landing & Teardown (`land`)

1. **Explicit Authorization Gate:** NEVER merge or discard a branch without explicit user approval.
2. **Pre-Merge Checks:** Run `scripts/pr_context.py`, `scripts/fetch_pr_feedback.py`, `scripts/inspect_pr_checks.py`, and `gh pr checks <n> -R <owner>/<repo> --required`. Confirm `OPEN`, not draft, `MERGEABLE`, zero unresolved `needs-human`/blocking threads, and required approvals/checks green. Update `BEHIND` only when branch protection requires it; resolve mechanical conflicts + re-test, escalate semantic conflicts.
3. **Merge & Verify:** Inspect allowed strategies via `gh repo view <o>/<r> --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed`, run `gh pr merge <n> -R <o>/<r> --squash|--merge|--rebase --delete-branch`, and confirm `state == "MERGED"`.
4. **Workspace Cleanup:** Check `git status --short` inside linked worktrees before removal. From `main_repo_root`, run `git -C "<main>" worktree remove "<wt>"`, `git worktree prune`, `checkout <base>`, `pull --ff-only`, and `branch -d "<branch>"` (only `-d` on `[gone]` sweeps).
5. **Safe Discard (Explicit Request Only):** Print branch, worktree path, and `git log <base>..HEAD --oneline`, and obtain explicit confirmation before `worktree remove` or `branch -D`.
