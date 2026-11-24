# Debug, Investigate & Compound Playbook

Use **`debug`** from attempt #1 whenever fixing an existing bug, failing test, runtime crash, or measured performance regression. Use **`investigate`** for read-only source walkthroughs or audits. Use **`compound`** after resolving a gnarly bug, hitting 3 failed debug attempts, seeing a recurring review defect, or authoring/pruning `AGENTS.md`.

## 1. `debug` Mode (Attempt-#1 4-Phase Root-Cause Loop)

Never guess from code reading alone when the failure can be reproduced:

1. **Reproduce `RED` & Observe Real Surface:**
   - Capture exact expected vs. observed behavior via a failing test/CLI or `chrome-devtools` (console, network, DOM, cookies, a11y, memory leaks).
   - Route failing remote PR checks to [pr-feedback-and-ci.md](pr-feedback-and-ci.md).
   - If local reproduction is impossible, state the missing environment condition explicitly without faking reproduction.
2. **Trace Backward to Fault Boundary:**
   - Trace backward from the visible symptom to the **first `file:line` where state diverges from the expected invariant**.
   - Use `git log -S` / `git blame` for regressions.
   - Separate environment hazards (stale `.exe` on Windows `PATH`, missing certs, dirty cache) from logic bugs.
3. **One-Hypothesis Loop (2-Strike Premise Check & 3-Strike Stop):**
   - Test **one** falsifiable hypothesis explaining all symptoms; revert failed trials cleanly instead of stacking guesses.
   - **2-Strike Check:** When two attempts fail in the same area, pause and test their shared assumption first.
   - **3-Strike Stop:** After **3 failed hypotheses/patches** against the same defect (counter never resets across minor tweaks), halt patching immediately:
     - Discard all trial edits.
     - Re-read the architecture and state transitions from scratch.
     - List each falsified hypothesis and why it failed.
     - Propose a structural fix or escalate to the user.
4. **Root-Cause Fix, Regression Pin & Delivery Check:**
   - Fix the originating defect (not a leaf symptom) and add a regression test asserting observable behavior.
   - Verify delivery via [review-and-verify.md](review-and-verify.md) (`scripts/win_binary_verify.py`, `chrome-devtools`, `scripts/diff_risk.py`).
   - **Performance bugs:** Capture a realistic baseline -> change one variable -> re-measure identically -> stop after 2 no-gain trials unless new evidence appears.

## 2. `investigate` Mode (Read-Only Research & Audits)

Investigation alone never authorizes file edits or git mutations:

- Search targeted symbols (`git grep`) before opening large files; stop expanding once answered with exact `file:line` citations.
- Separate *how* code works (traced from entry point -> state transitions -> output) from *why* it was built (`git log`, PRs, `AGENTS.md`, `docs/solutions/`), marking inferences explicitly.
- Use bundled audit CLIs for structured snapshots:
  - `scripts/hotspot_scan.py --repo "." --json` (modularity).
  - `scripts/repo_floor.py --repo "." --json` (CI/security floor).
  - `scripts/worktree_audit.py --repo "." --json` (worktrees/sync drift).

## 3. `compound` Mode (5-Rung Enforcement Ladder & Memory)

Promote recurring mistakes to the highest rung whose cost fits the risk. Prove every new lint or test fails against the real past mistake before keeping it.

| Rung | Mechanism | Rust | TypeScript / Next.js | Java / Spring | Python |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Architecture** | Single choke point makes bypass impossible | Channel/actor instead of shared `Arc<Mutex<T>>` | Validated gateway (`http.ts`) instead of raw `fetch` | Bounded executor rejecting overload with `429` | Pure typed pipeline stages isolated from I/O |
| **2. Types** | Make illegal states unrepresentable | Newtypes, closed `enum`, `Result` | Discriminated unions, strict `tsconfig`, zero `any` | Sealed interfaces, `record`, non-null contracts | Frozen `@dataclass`, `Literal`, strict `ty`/`pyright` |
| **3. Lint (names fix)** | Fail mechanically and name replacement | `clippy::unwrap_used` + workspace `deny` | `oxlint`/ESLint `no-restricted-imports` | Checkstyle/ArchUnit/SpotBugs rule | `ruff` (`BLE001`, `S` checks) with fix hint |
| **4. Behavior Test** | Empirically pin regression contract | `#[test]` on poison-lock/edge paths | Sanitizer XSS/idempotence unit tests | JUnit `409`/`429` + CI `skipped == 0` | `pytest` fixture reproducing edge input |
| **5. Docs / `AGENTS.md`** | Last resort for host/human truths | Windows `PATH`/SHA-256 install contract | Degraded-mode env flags & boundaries | Local external tool packaging rules | Physical/dataset observability limits |

### Solution Note Schema (`docs/solutions/<slug>.md`)

- Write a note **only** for non-obvious, recurring root causes or runtime traps (skip trivial typos).
- Search `docs/solutions/` before risky edits in familiar subsystems.
- Keep the body `<= 80` lines (`## Problem`, `## Root Cause`, `## Fix & Enforcer`, `## Verification`) with this exact YAML frontmatter:

```yaml
---
title: Concise descriptive title
date: YYYY-MM-DD
problem_type: bug | performance | security | build-env | concurrency | architecture
component: subsystem-or-module
severity: p1 | p2 | p3
symptoms:
  - Concrete error message or observable misbehavior
root_cause: One-sentence underlying technical cause
enforcer_rung: architecture | types | lint | test | docs
tags: [stack, subsystem]
---
```

### Minimalist `AGENTS.md` & Pruning Law

- Keep project `AGENTS.md` compact and command-backed: `## Architecture & Specs`, `## Behavioral Invariants`, `## Verification & Delivery` (exact commands + Windows `Get-FileHash` SHA-256 check if applicable), and `## Rule <-> Enforcer Ledger` (`Invariant / Past Failure | Rung | Concrete Enforcer`).
- **Pruning Law:** Whenever a prose rule moves up to Rungs 1–4, record the concrete enforcer in the ledger and **delete the redundant prose bullet**.
