# Worktree & Sync-Drift Hygiene Playbook

Use **`hygiene`** when creating an isolated git worktree, auditing or pruning stale/dirty/locked/detached-HEAD worktrees, checking local-vs-origin sync drift, or protecting ignored local configs during cleanup.

## 1. Non-Negotiable Safety Invariants

1. **Audit Read-Only First:** Always run the zero-mutation auditor before proposing any cleanup:
   ```bash
   python "<skill-dir>/scripts/worktree_audit.py" --repo "." --json
   ```
   - Exit `0`: All worktrees clean and synced with origin.
   - Exit `1`: Findings exist (prunable, drifted, dirty, locked, or unknown).
   - Exit `2`: CLI or git error on `stderr` — **stop immediately; never guess**.
2. **12-Fact Fact-Monad Delete Gate:** Delete or prune a worktree **only** when every one of the 12 safety facts (`exists_on_disk`, `locked`, `tracked_dirty`, `untracked_files`, `precious_ignored`, `other_ignored`, `disposable_ignored`, `upstream_drift`, `default_branch_drift`, `head_merged_into_default`, `detached_head_reachable`, `unreachable_reflog_commits`) reports `known: true` AND the worktree classifies as `SAFE_TO_PRUNE`. Any single `known: false` fact automatically downgrades the checkout to `UNKNOWN` so transient git errors or unresolvable default branches can never destroy work.
3. **Confirm Before Removing:** Present exact worktree paths, branch names, and classification buckets before running `git worktree remove` or `git branch -d`. Never pass `--force` or `git branch -D` without explicit user authorization.
4. **Protect Precious Ignored State:** Gitignored does **not** mean disposable. Hold `.env*`, private keys, local DBs, `.claude/settings.local.json`, and unclassified ignored directories (`.tools/`, `.data/`) as `DIRTY_HOLD`. Only reproducible caches (`node_modules`, `target`, `.next`, `__pycache__`) are disposable.
5. **Respect Locks & Detached Reflogs:** Never unlock locked checkouts or drop unreachable detached-HEAD or reflog commits without explicit user inspection.
6. **OS-Temp Scratch Only:** Keep temporary audit outputs, screenshots, and scratch files in OS temp (`$env:TEMP` / `$TMPDIR`), never inside `.audit/` or `.codex-run/`.

## 2. Worktree Startup (4-Gate Protocol)

Pass all four gates before editing application code in a new worktree:

1. **Gate 1 — Directory Selection:** Place worktrees outside the repository (`../<repo>-<slug>`) or inside a dedicated ignored root (`.wt/<slug>`). Never blindly duplicate heavy untracked toolchains (`.tools/`, `.data/`); reference or symlink shared paths only when genuinely required.
2. **Gate 2 — Ignore Check:** For any in-repo worktree root, verify:
   ```bash
   git check-ignore -q "<worktree-dir>"
   ```
   exits `0` **before** running `git worktree add`. If not ignored, add the directory to `.gitignore` or relocate outside the repo first.
3. **Gate 3 — Baseline Setup:** Install dependencies and explicitly copy or link required ignored local config (such as `.env.local`) without mutating the primary checkout.
4. **Gate 4 — Clean Test Baseline:** Run fast build/test checks before making code edits. If baseline tests already fail, report the exact failure and confirm with the user before proceeding so new regressions stay cleanly separable from pre-existing failures.

## 3. Worktree & Sync-Drift Audit Buckets

Run `python "<skill-dir>/scripts/worktree_audit.py" --repo "." --json` and act strictly by bucket and repo-level `default_branch_sync`:

| Bucket | Meaning | Allowed Action |
| :--- | :--- | :--- |
| **`SAFE_TO_PRUNE`** | All 12 facts `known: true`, merged into default branch, unlocked, clean (at most disposable caches), zero unreachable reflog commits | Confirm paths/branches with user, then run `git worktree remove "<path>"` and optionally `git branch -d "<branch>"` |
| **`NEEDS_SYNC`** | Clean and unlocked, but drifted vs. upstream or default branch | If clean default-branch (`main`/`master`) checkout is behind `origin`, offer `git pull --ff-only` inside that worktree after confirmation. For unmerged feature branches, report drift and route pushes/updates to [pr-ship-and-land.md](pr-ship-and-land.md) (never push or rebase unconfirmed) |
| **`DIRTY_HOLD`** | Tracked/untracked edits, precious or unclassified ignored files, locked checkout, or unreachable detached/reflog commits | **Never prune.** Surface exact files, lock reasons, and unreachable SHAs for user decision |
| **`UNKNOWN`** | One or more safety facts failed (`known: false`) or default branch could not be resolved | **Halt cleanup** for that checkout and report every `known: false` fact verbatim |
| **`CLEAN_ACTIVE`** | Active clean checkout (including primary checkout) | Keep untouched |

## 4. Reporting Standard

Close every `hygiene` run with:
- **Audit & Sync Summary:** Exact counts per bucket (`CLEAN_ACTIVE`, `SAFE_TO_PRUNE`, `NEEDS_SYNC`, `DIRTY_HOLD`, `UNKNOWN`) and local-vs-origin `default_branch_sync` drift.
- **Executed Actions & Holds:** Exact commands run, paths created/removed, and all `DIRTY_HOLD` / `UNKNOWN` items awaiting user confirmation.
