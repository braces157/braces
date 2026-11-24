# Reframe & Sharpen Playbook

Use **`reframe`** and **`sharpen`** for coding tasks where the idea is still rough, hard to explain, or locked into a premature solution. Both modes are **read-only**: never edit source files until the user approves the sharpened prompt.

## 1. Smart Auto + On-Demand Gate

- **Always enter (on demand):** User says `sharpen`, `reframe`, *"help me explain this"*, or asks to clarify/rewrite a coding prompt before building.
- **Auto-enter (selectively):** A coding request is genuinely vague, contradictory, missing a verifiable outcome, or jumps straight to a heavy mechanism (*"maybe add Redis/queues/microservices so it feels better"*) where guessing wrong causes expensive rework.
- **Skip immediately:** The target files, error, or expected behavior are already clear (*"fix this stack trace"*, *"add `--json` to `export.py`"*), or the user says *"just build it"*. Go straight to **`build`** ([build-and-refactor.md](build-and-refactor.md)) or **`debug`** ([debug-and-compound.md](debug-and-compound.md)).

## 2. `reframe` Mode (Break Out of a Stuck or Solution-First Frame)

Run this first only when the prompt prescribes a mechanism before naming the real pain, or when every option inside the current framing feels bad:

1. **Name what the current framing fixes:** its **subject** (component/user in focus), **level** (symptom vs mechanism vs workflow), and **scope** (what is held constant).
2. **Change exactly one** axis and restate the engineering problem in **one plain sentence**. Prefer the shift that makes stuck options irrelevant over picking between them.
3. **Anti-word-salad self-test:** Before replying, check whether the new sentence still names the same subject at the same level. If so, it is the old frame in new words — discard it and change a different axis once. If the second attempt still fails the test, report that **the current frame holds** and move straight to `sharpen`.
4. **Deliver:** One reframed problem sentence plus one line on what it unlocks that the old framing blocked.

## 3. `sharpen` Mode (*Deeper, Not Bigger*)

Turn a rough coding thought into a tight, expert-grade prompt **without inflating scope**:

1. **Ground in Raw Material First (Read-Only):** Inspect the real entry points, types, configs, or logs in the repo before rewriting — never sharpen from guesses when ground truth is on disk.
2. **Run the 4-Part Depth Pass** (one crisp line each):
   - **Expert gap:** What a strong engineer would guard against here that went unmentioned (edge cases, invariants, repo precedent, failure modes) — without adding new features.
   - **Raw starting files:** Exact repo paths/symbols/logs to inspect first.
   - **Method & boundaries:** Concrete approach plus an explicit **Non-goals** line keeping scope tight.
   - **Outside check:** Objective verification independent of agent self-judgment (failing-to-passing test, CLI exit code, typecheck, or live DOM/network check).
3. **Split Technical Guesses from Human Choices:**
   - **Testable technical questions** (API shape, library behavior, perf): write into the sharpened prompt that the agent should **probe or prototype and pick by evidence** — do not quiz the user on internals.
   - **Product / UX / preference calls** (choices no experiment can settle): surface at most **1–2 concrete choices** (`A` / `B`) with a recommended default so the user can answer in five seconds.
4. **Return the Rewritten Prompt and Stop:**
   - **Reframe** *(only if framing moved)*: 1 sentence + what it unlocks.
   - **Sharpened Prompt:** 4–8 crisp lines covering *Goal*, *Start From*, *Constraints / Non-goals*, *Edge Cases*, and *Verification Check*.
   - **Open Choice(s)** *(0–2 max)*: Only real human calls with defaults.
   - Close with one line: *"Reply `go` to run this, pick a choice, or tweak any line."*

## 4. Handoff & Guardrails

- After approval: route small/clear work to **`build`** ([build-and-refactor.md](build-and-refactor.md)) or **`debug`** ([debug-and-compound.md](debug-and-compound.md)); route multi-component architecture or UI design to **`office-hours` / `design`** ([office-hours-and-design.md](office-hours-and-design.md)).
- If implementation or multi-candidate exploration wildly diverges, pause and re-run **`reframe`** instead of patching over a broken frame.

| Excuse | Reality |
| :--- | :--- |
| "I get the gist — let me start coding and ask later." | Guessing an under-specified goal causes rework; a 20-second sharpened prompt prevents building the wrong thing. |
| "Sharpening means adding extra features and layers." | Deeper != bigger. Tighten edge cases, raw inputs, and outside checks while keeping scope narrow. |
| "Let me rewrite this clear prompt too, just in case." | Auto-trigger only on genuine vagueness or solution-first traps; never nag on specific tasks. |
