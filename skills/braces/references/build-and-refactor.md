# Build & Architecture Refactoring Playbook

Use **`build`** for features, behavior-preserving refactors, and migrations. Use **`refactor`** when decomposing god files (`>600` soft / `>1,000` hard prod LOC), collapsing shallow pass-through wrappers, auditing size/churn hotspots, or tightening error boundaries.

## 1. `build` Mode (Vertical Verifiable Slices)

Small, clear edits need no written plan. For substantive implementation:

1. **Context, Worktree & Live Docs:**
   - Read the entry point, domain types, nearby conventions, and existing tests first.
   - Use an isolated worktree if the primary tree has unrelated dirty work (4-gate startup in [worktree-hygiene.md](worktree-hygiene.md)).
   - For fast-moving frameworks or SDKs (Next.js, React, Spring Boot, Tailwind, etc.), query live versioned docs via `context7-mcp` (`resolve-library-id` -> `query-docs`) instead of guessing.
   - **Proactive Modularity Check:** Before adding responsibilities to a file past **600 prod LOC** (hard ceiling **1,000**), run `scripts/hotspot_scan.py` and carve a cohesive deep-module seam first.
2. **Implement Surgically:**
   - Enforce domain invariants in types/enums and validate external inputs at trust boundaries.
   - Build one runnable vertical slice at a time; skip speculative abstractions, pass-through wrappers, and drive-by reformatting.
   - Run focused tests after each slice, then full project verification (`cargo test`/`clippy`, `npm test`, `mvn test`, `pytest`).

## 2. `refactor` Mode (Deep Modules & Hotspot Decomposition)

### Preflight Hotspot Survey

Measure actual production LOC, git churn, and polyglot smells before proposing edits:

```bash
python "<skill-dir>/scripts/hotspot_scan.py" --repo "." --soft-limit 600 --hard-limit 1000 --top 15 --json
python "<skill-dir>/scripts/hotspot_scan.py" --repo "." --html-report --json
```

- Separates Rust inline `#[cfg(test)]` blocks and standalone test files from production LOC so well-tested files are never misclassified as monoliths.
- `--html-report` writes self-contained HTML + Mermaid strictly into OS temp (`html_report_path` in JSON), keeping `git status` untouched.
- Exit codes: `0` = clean, `1` = hard-limit violation or high-risk smell (`rust_lock_unwrap`, `rust_unsafe`, `ts_ignore`, `py_bare_except`, `py_broad_except`, `java_broad_catch`), `2` = CLI/path error on `stderr`.

### Core Architectural Invariants

1. **600 Soft / 1,000 Hard Prod LOC Ceiling:** Never stack new responsibilities onto a touched file past 1,000 prod LOC; carve a cohesive seam first.
2. **Ousterhout Deep Modules & the Deletion Test:**
   - Prefer fewer *deep modules* (narrow, stable interfaces hiding messy state, concurrency, or I/O) over shallow forwarding helpers.
   - Apply the **Deletion Test:** collapse any wrapper whose removal leaves callers just as simple; keep a seam only when callers would otherwise duplicate invariants, locking, serialization, or error handling. Keep public or compatibility wrappers used by external callers unless removal is authorized.
3. **Subtract-Before-Add ("Code Judo"):**
   - Delete dead branches, unreachable flags, duplicate DTOs, and redundant plumbing *before* creating new files so new interfaces never freeze accidental complexity.
4. **Characterization-Test Pin First:**
   - Pin observable behavior before moving code across boundaries:
     - Happy paths.
     - Edge, empty, Unicode, and Windows path inputs.
     - Failure and fallback paths.
     - Side-effect order and idempotence.
   - Confirm all pins pass green before structural edits. **Typecheck and lint are not a pin.**
5. **Scoped Error & Rescue Map:**
   - Eliminate silent catch-alls and poison panics across modified boundaries using `trigger -> error type -> recovery/fallback -> caller-visible outcome`.
   - Log pre-existing out-of-scope catch-alls as follow-ups instead of making drive-by edits.
6. **Bisectable Commits:**
   - Order commits `leaf types/domain -> extracted modules -> thin facade wiring` so each commit compiles and passes pinned tests standalone.

### Polyglot Extraction Playbooks

- **Rust (e.g. a 1,500-line service or controller module):**
  - Keep a thin facade (`<200` prod LOC) backed by cohesive private submodules (`state.rs`, `parser.rs`, `store.rs`, `render.rs`, `jobs.rs`).
  - Keep unit tests inline under `#[cfg(test)]` and promote multi-module pins to `tests/`.
  - Replace `.lock().unwrap()` poison panics with typed `Result`, single-owner channel/actor loops, or explicit recovery (`.unwrap_or_else(|e| e.into_inner())`) that validates or rebuilds the invariant before reuse (propagate an error if validation fails).
  - Isolate `unsafe` FFI/OS calls inside one tiny adapter with a `// SAFETY:` invariant on every block.
- **Next.js / React (`page.tsx` RSC):**
  - Keep `page.tsx` thin: route params, parallel `Promise.all` fetches, auth/404 guards, and top-level composition.
  - Extract sorting, sanitization, and formatting into pure server `lib/` modules.
  - Push `"use client"` to minimal interactive leaf islands receiving narrow serializable DTOs — zero `: any`, `@ts-ignore`, or wildcard barrel re-exports.
- **Java / Spring (e.g. a large `*Service.java` god class):**
  - Extract authorization rules and domain invariants into framework-free POJO policies tested with fast JUnit (no Spring boot).
  - Split read queries, write commands, and external HTTP/subprocess/runtime gateways with explicit timeouts and backpressure.
  - Replace `catch (Exception)` with domain exceptions mapped in `@RestControllerAdvice`; always restore `InterruptedException`.
- **Python Pipelines:**
  - Separate pure stateless stages (typed `@dataclass`/`TypedDict` transforms tested on synthetic inputs) from stateful I/O or model runners.
  - Replace bare `except:` and broad `except Exception:` with concrete typed failure records while letting programmer bugs (`TypeError`, `KeyError`, `ValueError`) surface.

### Workflow & Completion Summary

- **Sequence:** Measure -> Subtract & Pin -> Design Deep Seams -> Execute One Slice at a Time -> Re-Scan & Verify (`scripts/hotspot_scan.py`).
- **Report:** **Hotspot Delta** (before/after prod LOC, test LOC, smells), **Seam & Error Map**, **Verification** (exact commands + exit codes), and **Artifacts** (bisectable commit SHAs + OS-temp `html_report_path` if generated).
