# Build

Use for a feature, behavior-preserving refactor, migration, or multi-step implementation. Small clear edits need no written plan.

## Establish the change

Read the entry point, affected data shape, nearby conventions, and relevant tests. Describe the observable result and a completion check. For a refactor, identify behavior and public contracts that must stay stable. For a migration, identify callers and the final state before editing.

Resolve ordinary type, ownership, and module decisions inline. Open the design playbook only when a concrete uncertainty could cause costly rework. Clarify a product choice when the user's intent cannot be inferred or tested. Continue unrelated work while awaiting the answer.

## Implement in verifiable units

Make the smallest complete change. Use domain structures that make invalid states difficult to express. Validate external input at boundaries. Avoid unrelated abstractions and speculative compatibility layers. Migrate callers in the same unit when practical, then remove the superseded path.

For a large migration, choose one representative slice and verify it before repeating it. Use a script or codemod when repetition justifies one. Keep intermediate states reviewable without building disposable architecture solely for the transition. Preserve a working checkpoint and recovery steps when an operation can partially complete.

Delegate only when ownership and acceptance checks are separable. The parent can implement the central path while a helper handles an independent module. If a required review comes later, reserve its budget slot before assigning implementation. A single feature need not have separate explorer, designer, implementer, comment reviewer, and final reviewer agents.

## Verify and finish

For behavior changes, use existing tests and add meaningful missing coverage. Run the focused checks first, then required project checks. For a UI or CLI, exercise the actual user interaction. For a refactor, inspect for accidental API or behavior changes. Rebuild and install only when the task or project requires it, and verify the executable the user will launch.

Open the review-and-deliver playbook when external review or publication becomes part of the requested result. Creating a PR is not an automatic next step for a local edit. A long task may keep a compact progress note; it does not need an agent to audit that note.

Finish when the requested behavior and delivery checks are satisfied. Record remaining limitations rather than expanding into adjacent cleanup.
