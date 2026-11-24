# Skill Forge Playbook (Authoring, Evals & Validation)

Use **`forge`** whenever creating a new portable `agentskills.io` skill, auditing or tightening an existing `SKILL.md`, debugging trigger misfires, pressure-testing discipline skills, authoring `evals/evals.json`, or validating/syncing skill packages across Antigravity, Claude Code, and Codex.

## 1. Preflight Structural Validation & Cross-Agent Sync

Run the zero-dependency validator before finishing any skill edit or audit:

```bash
python "<skill-dir>/scripts/validate_skill.py" <target-skill-dir>
python "<skill-dir>/scripts/validate_skill.py" <skills-parent-dir> --all --json
```

- Semantic exit codes: `0` = valid (warnings allowed), `1` = structural, path, clutter, eval, or word-cap errors, `2` = CLI or path error on `stderr`.
- When syncing a skill repo across harnesses, run `python -B tools/sync_skills.py --target antigravity|claude|codex|agents|all [--dry-run] [--json]`.

## 2. The 5 Authoring Pillars

1. **Three-Tier Progressive Disclosure & Lean-by-Default:**
   - **Tier 1 (Frontmatter, always loaded):** Portable `agentskills.io` keys only (`name`, `description`, optional `license`, `compatibility`, `metadata`, `allowed-tools`). `name` must match the directory name in lowercase hyphen-case (`<= 64` chars).
   - **Tier 2 (`SKILL.md`):** Target `<= 950` words (hard cap `1,250`). Hold invariants, script contracts, and routing only. Start single-file; add a mode router only when genuinely distinct modes exist.
   - **Tier 3 (On-demand resources):**
     - Split deep playbooks into `references/<topic>.md` (`<= 880` words each) with explicit *when to read* triggers.
     - Move repeatable deterministic checks into pure Python 3 stdlib `scripts/<tool>.py` (`--json`, exit `0`/`1`/`2`).
     - Store templates in `assets/<template>`.
     - Exclude `README.md`, changelogs, `.DS_Store`, `Thumbs.db`, and `__pycache__` / `*.pyc` from runtime skill folders.
2. **CSO Trigger Engineering & Negative Boundaries:**
   - Frame `description` in third-person/imperative (`Use whenever...`) packed with natural user intents, error symptoms, and synonyms (`<= 640` chars, zero `<` or `>`).
   - Include an explicit negative boundary (`Do NOT use when...`) naming near-miss sibling tasks or plain chat.
   - **Never summarize internal workflow steps in `description`.** Previewing procedure steps causes models to act straight from frontmatter and skip `SKILL.md` safety invariants.
3. **Explain the *Why*, Calibrate Freedom, Preserve Scope:**
   - Delete generic software advice and duplicated prose; keep each procedure in one single source of truth.
   - Replace brittle `MUST/ALWAYS` walls with concise rationales so models generalize accurately to novel edge cases.
   - Match specificity to risk: *High freedom* (goal + acceptance criteria) for judgment tasks; *Medium freedom* (decision tables, schemas) for structured outputs; *Low freedom* (deterministic scripts + binary gates) for data-loss, security, or drift hazards.
   - Never silently expand mutation permissions.
4. **Skill TDD (`RED -> GREEN -> REFACTOR`) & Stacked Pressure Testing:**
   Combine **at least 3 orthogonal pressures** in realistic scenarios: (a) time urgency (*"release cut in 10 min"*), (b) sunk cost/partial proof (*"I eyeballed the diff and lint passed"*), (c) scope/authority minimization (*"4-line hotfix, skip the heavy check"*), and (d) environmental friction (dirty tree, tempting `--no-verify` or `-DskipTests`).
   - **RED:** Run a clean subagent *without* the skill against the stacked scenario and record verbatim rationalizations.
   - **GREEN:** Add the smallest invariant plus *why* rationale closing each failure and map verbatim excuses into an `Excuse | Reality` table.
   - **REFACTOR:** Re-test with a **new** stacked scenario (different details, same temptation); patch root principles rather than hyperspecific ban lists.
5. **Empirical Evals, 60/40 Holdout, Blinding & Forward-Testing:**
   Ship `evals/evals.json` (checked by `scripts/validate_skill.py`):
   ```json
   {
     "skill": "my-skill",
     "trigger_tests": [
       {"prompt": "Natural positive request", "should_trigger": true, "split": "train"},
       {"prompt": "Near-miss sibling request", "should_trigger": false, "split": "holdout"}
     ],
     "forward_tests": [
       {"id": "scenario-slug", "prompt": "Natural prompt, zero hints", "invariants": ["Script exited 1", "Clean git tree"]}
     ]
   }
   ```
   - **60/40 Trigger Holdout:**
     - Author 12–16 realistic prompts (~50% positive phrased like real chat, ~50% near-miss negative sharing vocabulary — never trivial haikus), balanced 60% `train` / 40% `holdout`.
     - Tune `description` on `train`; accept changes **only** when `holdout` precision and recall improve or hold without copying holdout wording into `description`.
   - **Anti-Leakage Blinding:**
     - Ban leakage tokens (`eval`, `judge`, `rubric`, `benchmark`, `arena`, `candidate`, `score`, `grading`, `test-harness`) in forward prompts, branches, and temp paths (except meta-skill domains).
     - Use realistic temp workspace names (`<temp>/acme-api-fix/`), grade actual tool traces and disk artifacts rather than self-reports, and strip revision labels in blind A/B runs.
   - **Independent Forward-Testing & Quota Fallback:**
     - Run a clean subagent with zero prior context, only the natural prompt, the skill, and isolated OS-temp fixtures.
     - Assert **observable invariants** (exit codes, files, git state) — never prose regexes — recording `PASS`, `FAIL`, or `UNPERFORMED`.
     - Under tight quota/budget forbidding subagents, run deterministic script/invariant tests locally and mark subagent forward tests **`UNPERFORMED`**.

## 3. Completion Checklist

Before declaring any skill ready:
1. `scripts/validate_skill.py` exits `0` with zero errors/warnings.
2. `evals/evals.json` has balanced `train`/`holdout` triggers and observable `forward_tests`.
3. Unit tests and forward tests (or scripted checks + `UNPERFORMED` notice) ran fresh.
4. Report exact commands and exit codes.
