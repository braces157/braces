# Office Hours & Design Playbook

Use **`office-hours`** when an idea, feature request, or problem statement is still rough and you want to challenge premises, isolate the highest-leverage vertical wedge, and lock a verifiable plan before writing code. Use **`design`** when a specific architecture boundary or UI interaction choice is uncertain enough to cause rework.

## 1. `office-hours` Mode (Premise Challenge & Vertical Wedge)

Do not jump straight into editing code during `office-hours`. Run this fast 4-step framing pass in the current chat:

1. **Challenge the Premise & Name the Real Bottleneck:**
   - Identify the concrete user pain, defect class, or workflow drag this solves.
   - Ask what happens if we do nothing or delete an existing layer instead (*Subtract-Before-Add*).
   - Push back constructively if the proposed mechanism (extra services, queues, speculative abstractions) is over-engineered for the actual outcome.
2. **Pick the Narrowest Verifiable Wedge:**
   - Separate the **core vertical tracer bullet** (smallest end-to-end slice proving real value today) from speculative nice-to-haves.
   - Cut horizontal layer plans ("build all schemas, then all services, then all UI"); every slice must run and be verifiable standalone.
3. **Sketch Data Shapes, ASCII Flow & Failure Map:**
   - Define domain types and state ownership first so illegal states cannot compile.
   - Draw a compact ASCII flow diagram of the happy path.
   - Add a 3-column failure map: `Failure / Bad Input | Recovery Action | User-Visible Outcome`.
4. **Lock the Execution Handoff:**
   - Close with a 3–5 bullet action plan listing target files and exact verification command.
   - Name required follow-on modes: `build` / `refactor` in [build-and-refactor.md](build-and-refactor.md) if extending `>600` prod LOC files; `review` / `verify` in [review-and-verify.md](review-and-verify.md) before shipping.

## 2. `design` Mode (Resolving Architecture & UI Uncertainty)

Ordinary function extraction or following an established repository pattern needs no design phase. Enter `design` only when a wrong boundary or interaction choice would force expensive rework:

1. **Resolve Uncertainty with Evidence:**
   - Name the decision, repository constraints, existing precedent, and cost of reversing a wrong choice.
   - Compare **at most two plausible approaches** against concrete project criteria (compatibility, failure handling, reader load, maintainability); never invent strawman options.
   - Run the smallest read-only probe or disposable spike for observable technical questions; ask one focused question for product preference calls.
2. **Deep Modules Over Shallow Pass-Throughs:**
   - Prefer narrow, stable interfaces hiding messy state, concurrency, or I/O over splitting logic across shallow forwarding wrappers.
   - Before extending a monolith (`>600–1,000` prod LOC), run `python "<skill-dir>/scripts/hotspot_scan.py" --repo "." --json` and follow [build-and-refactor.md](build-and-refactor.md).
   - For fast-moving frameworks or SDKs (Next.js, React, Spring Boot, Tailwind, etc.), query live versioned docs via `context7-mcp` (`resolve-library-id` -> `query-docs`) instead of guessing from memory.
3. **Optional Second Perspective:**
   - A consequential concurrency model, security boundary, or one-way migration justifies at most one focused review helper within the active task budget.
   - Record the chosen shape and key tradeoff, then transition cleanly to `build`.

## 3. UI Craft, Token Grounding & 6 Edge States

Ground every UI design decision in explicit project tokens rather than generic utility defaults:

- **Token Grounding (`DESIGN.md`):**
  - Inspect or maintain one concise `DESIGN.md` defining palette roles (canvas, surface, ink, accent, semantic status), typography pair, spacing scale, elevation limits, and motion budget.
  - Bind tokens via CSS custom properties (`--font-*`, `--surface-*`, `--accent-*`), `next/font`, or Tailwind `@theme`.
- **Zero AI-Slop Tells:**
  - Avoid ungrounded purple-indigo hero gradients, unconfigured `Inter`/`system-ui` stacks without tokens, nested card-inside-card boxes, low-contrast muted gray text on tinted fills, and skipped heading levels (`<h1>` -> `<h3>`).
- **Design All 6 Edge States Up Front:**
  1. **Empty:** Zero-item views show clear guidance and a direct next action (never blank boxes, raw `0`, or `NaN`).
  2. **Loading:** Geometry-matched skeletons or indicators with zero jarring layout shift.
  3. **Overflow & Density:** Survive long unbroken strings, accented/CJK text, `100+` rows, desktop (`1280px`), and narrow mobile (`375px`) with zero clipped controls or accidental horizontal scroll.
  4. **Error & Offline:** Failed API responses or malformed payloads render actionable recovery states with zero white-screen crashes.
  5. **Keyboard & Focus:** Complete `Tab`, `Shift+Tab`, `Enter`, `Space`, and `Escape` traversal, visible `:focus-visible` outlines, accessible labels (`aria-label`, `title`, or `sr-only`) on every icon-only `<button>`, and focus return on dialog close.
  6. **Contrast:** `>= 4.5:1` for body copy and `>= 3:1` for large text and UI chrome across default, hover, active, and disabled states.
- **Verification Handoff:**
  - Remember **screenshot != interaction** and **build != runtime**.
  - Run `python "<skill-dir>/scripts/ui_slop_check.py" --repo "." --json` and verify real flows in `chrome-devtools` (saving proof screenshots strictly under OS `%TEMP%`) per [review-and-verify.md](review-and-verify.md).
