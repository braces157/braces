# Braces

A single unified, zero-dependency `agentskills.io` engineering skill (**`braces`**) backed by 8 progressive-disclosure playbooks, 11 pure Python 3 stdlib verification & GitHub PR CLIs, cross-agent sync tooling, and an ultra-compact global Antigravity guardrail (`GEMINI.md.template`) tailored for polyglot Rust, Next.js/TypeScript, Java/Spring Boot, Python, and Windows desktop workflows.

## Unified `braces` Architecture (`skills/braces/`)

Everything lives in **one single self-contained skill** ([skills/braces/SKILL.md](skills/braces/SKILL.md)) with zero external skill dependencies and zero trigger collisions:

| Mode | On-Demand Playbook (`skills/braces/references/`) | Deterministic Stdlib Scripts (`skills/braces/scripts/`) |
| :--- | :--- | :--- |
| **`office-hours` / `design`** | [office-hours-and-design.md](skills/braces/references/office-hours-and-design.md) — premise challenge, vertical scope wedge, ASCII flow + failure map, `DESIGN.md` tokens, 6 edge states | `ui_slop_check.py` (+ optional `context7-mcp`, `chrome-devtools`) |
| **`build` / `refactor`** | [build-and-refactor.md](skills/braces/references/build-and-refactor.md) — vertical slices, 600L/1,000L prod LOC ceiling, Ousterhout deep modules, Deletion Test, Subtract-Before-Add, characterization pinning, scoped Error & Rescue Map | `hotspot_scan.py` (splits Rust `#[cfg(test)]` from prod LOC, git churn, polyglot smells, OS-temp HTML/Mermaid report) |
| **`debug` / `investigate` / `compound`** | [debug-and-compound.md](skills/braces/references/debug-and-compound.md) — attempt-#1 4-phase root-cause loop, 2-strike premise check, 3-strike architecture stop, 5-rung Enforcement Ladder, `docs/solutions/` YAML schema, minimalist `AGENTS.md` | `hotspot_scan.py`, `repo_floor.py`, `worktree_audit.py` |
| **`hygiene`** | [worktree-hygiene.md](skills/braces/references/worktree-hygiene.md) — 4-gate git worktree startup, read-only 12-fact Fact-Monad delete gate (`SAFE_TO_PRUNE \| NEEDS_SYNC \| DIRTY_HOLD \| UNKNOWN \| CLEAN_ACTIVE`), precious ignored-file protection | `worktree_audit.py` |
| **`review` / `verify`** | [review-and-verify.md](skills/braces/references/review-and-verify.md) — Step 1.5 scope drift, risk-tier diff audit, `git blame` on removed guards, blast radius, adversarial Refuting Verifier, 5-Level Proof Ladder, `AUTO-FIX` vs `ASK`, repo CI/Actions floor, Windows `.exe` `PATH` SHA-256 parity & shadowing, Web UI anti-slop + OS-temp browser proof | `diff_risk.py`, `repo_floor.py`, `win_binary_verify.py`, `ui_slop_check.py` |
| **`ship` / `pr` (Create & Land)** | [pr-ship-and-land.md](skills/braces/references/pr-ship-and-land.md) — diff-grounded `--body-file` PR/commit prose, template compliance, bisectable commits, safe push, merge verification, linked worktree teardown | `pr_context.py`, `fetch_pr_feedback.py`, `inspect_pr_checks.py` |
| **`ship` / `pr` (Feedback, CI & Shepherd)** | [pr-feedback-and-ci.md](skills/braces/references/pr-feedback-and-ci.md) — 6-verdict anti-sycophancy CodeRabbit/human triage, pending-review & stale-SHA guards, remote GitHub Actions CI repair, bounded 2-cycle shepherd loop | `pr_context.py`, `fetch_pr_feedback.py`, `inspect_pr_checks.py`, `thread_actions.py` |
| **`forge`** | [skill-forge.md](skills/braces/references/skill-forge.md) — 5-pillar authoring, 60/40 holdout trigger evals, anti-leakage blinding, Independent Forward-Testing + quota fallback, `RED -> GREEN -> REFACTOR` pressure tests | `validate_skill.py` |

## Repository Layout

```text
braces/
├── ATTRIBUTION.md                # Upstream attribution notes
├── GEMINI.md.template            # Copy to ~/.gemini/GEMINI.md (<60 lines)
├── LICENSE                       # MIT
├── README.md
├── skills/
│   └── braces/                   # Single unified skill
│       ├── SKILL.md              # Front-door router, budgets, verification laws (<=950w)
│       ├── references/           # 8 progressive playbooks (each <=880w)
│       ├── scripts/              # 11 pure Python 3 stdlib CLIs
│       └── evals/evals.json      # Balanced 60/40 trigger & forward-test invariants
├── tools/
│   ├── check.py                  # CI entrypoint wrapping validate_skill.py & doc link checks
│   └── sync_skills.py            # Validated, idempotent cross-agent installer
└── tests/                        # 65-test stdlib unittest suite
```

## Quick Start

### 1. Clone and Validate the Unified Skill
```bash
git clone https://github.com/braces157/braces.git
cd braces
python -B tools/check.py
python -B skills/braces/scripts/validate_skill.py skills --all --json
```

### 2. Run the Full Test Suite (65 Tests)
```bash
python -B -m unittest discover -s tests -v
```

### 3. Sync `braces` Cross-Agent
`tools/sync_skills.py` validates `skills/braces/` via `validate_skill.py` before copying, strips `__pycache__` / `*.pyc` / `README.md` clutter, and prunes stale files inside the destination `braces/` folder idempotently.

```bash
# Preview changes without touching disk
python -B tools/sync_skills.py --target antigravity --dry-run

# Sync unified braces into Antigravity (~/.gemini/config/skills/braces)
python -B tools/sync_skills.py --target antigravity --json

# Sync into Claude Code, Codex, ~/.agents/skills, or all targets at once
python -B tools/sync_skills.py --target claude
python -B tools/sync_skills.py --target codex
python -B tools/sync_skills.py --target agents
python -B tools/sync_skills.py --target all
```

### 4. Install the Global Antigravity Guardrail
Inspect [GEMINI.md.template](GEMINI.md.template), adjust any personal preferences, and copy it to `~/.gemini/GEMINI.md` (Windows: `Copy-Item .\GEMINI.md.template "$HOME\.gemini\GEMINI.md"`).

## Prior Art & License

All prose and Python stdlib code here is written fresh; no upstream files were copied. Design ideas and engineering mechanisms were synthesized from:

- **`Poteto/pstack`** & **`michael-denyer/pstack-claude`** — Enforcement Ladder, 1,000-line file ceiling, Subtract-Before-Add, 5-level proof ladder, Fact-Monad worktree audit, blinded eval rules, and prose-path validation.
- **`garrytan/gstack`** — `AUTO-FIX` vs `ASK` autonomy split, Error & Rescue Map, 3-strike architecture stop, Scope Drift checks, and bisectable commits.
- **`EveryInc/compound-engineering-plugin`** — Institutional memory loop (`docs/solutions/` YAML frontmatter) and promoting recurring findings into `AGENTS.md`.
- **`mattpocock/skills`** — Ousterhout deep modules, the Deletion Test, and OS-temp self-contained HTML/Mermaid architecture reports.
- **`karpathy-guidelines`** — 4-pillar compact behavioral core (Think Before Coding, Simplicity First, Surgical Changes, Goal-Driven Execution).
- **`obra/superpowers`** — Skill TDD (`RED -> GREEN -> REFACTOR`), stacked pressure testing, `Excuse | Reality` anti-rationalization tables, CSO anti-shortcut rule, 4-gate worktree startup, and fresh-evidence verification gates.
- **`trailofbits/skills`** — Risk-tier differential review, `git blame` invariant checks, blast-radius analysis, adversarial Refuting Verifier, and Rust/footgun safety lenses.
- **`pbakaus/impeccable`** — Deterministic zero-LLM UI anti-slop checks, token grounding (`DESIGN.md`), and edge-state hardening.
- **`vercel-labs/agent-skills`** — Impact-ranked React/Next.js waterfall, barrel-import, and RSC boundary rules.
- **`anthropics/skills`** (`skill-creator`) — Explain-the-*why* authoring, 60/40 train/holdout trigger tuning, and blind evaluation discipline.
- **OpenAI Codex `skill-creator`** — Preserve user intent/scope, degrees of freedom, Independent Forward-Testing on observable invariants, and runtime folder cleanliness.
- **`steipete/agent-scripts`** — Canonical cross-harness skill sync and prompt-budget discipline.

See [ATTRIBUTION.md](ATTRIBUTION.md) and [LICENSE](LICENSE).
