# Design

Use when a specific architecture or interaction choice is uncertain enough to affect implementation. Ordinary function extraction or use of an established project pattern does not require a design phase.

## Resolve the uncertainty

Name the decision, constraints, existing precedent, and the consequence of a wrong choice. Sketch the data types, ownership, boundaries, and user path at the level needed to test the choice.

When alternatives matter, compare two plausible approaches in the current chat. Use criteria tied to the project, such as compatibility, user effort, failure handling, and maintainability. Select one when the evidence is enough. Do not invent candidates to fill a panel.

For an observable question, build the smallest disposable prototype that answers it. For a genuine preference call, use available user context or ask a focused question. Avoid completing a whole alternative implementation just to compare one uncertain interaction.

## UI work

Inspect the existing interface and relevant user references. Choose one visual or interaction specialist that fits the requested style and platform. Keep framework and platform requirements intact. Review the result at actual dimensions, exercise important states, and check keyboard, loading, empty, and failure behavior where relevant.

A screenshot verifies appearance. It does not verify interaction. A successful build verifies compilation. It does not verify either appearance or interaction. Use the appropriate app driver when available and disclose missing inspection.

## Optional second perspective

A consequential unresolved boundary, concurrency model, or interaction tradeoff can justify one focused design reviewer. Give it the decision and evidence, not the entire task to redesign. Reconcile disagreements with source or a targeted experiment. Do not automatically create a third judge or then commission new implementation designs.

When the user explicitly asks for architect, arena, or another multi-agent design workflow, inspect that skill and budget its smallest valid team before dispatch. Keep the parent's control of dispatch, the host capacity limit, and any tighter user budget. If those prevent the full workflow, state the limit instead of silently calling a partial run complete.

Record the chosen shape and only the tradeoff needed by implementation. Continue through the build playbook without reopening settled choices unless new evidence changes them.
