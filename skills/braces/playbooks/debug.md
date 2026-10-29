# Debug

Use for a defect, failed check, runtime symptom, or measured performance problem. A diagnosis request ends with evidence and an explanation unless a fix is also authorized.

## Reproduce and narrow

Establish expected and observed behavior. Reproduce on the affected UI, CLI, test, or service when practical. If reproduction is unavailable, state the missing condition and investigate the supplied evidence without claiming a reproduction.

Trace from the symptom to the first incorrect state. Form one testable hypothesis and run the cheapest discriminating check. Inspect relevant logs and callers, not the whole repository. When two attempts fail on the same premise, test the premise before adding another guard or workaround.

For a runtime issue, inspect the actual process and relevant resource or trace. For failing CI, find the failing job and first causal error. Distinguish environment failures from application failures. Use the source and configuration for that revision.

## Fix and verify

Add a regression test when there is a useful, affordable target. Prefer assertions about observable behavior. Do not build a large test framework to justify a tiny edit. A legitimate edge case can need a guard; corrupted internal state usually needs its originating logic fixed.

Change the narrow cause, reproduce the original path again, and run relevant surrounding checks. Delegate one substantial independent cause investigation only when it could resolve a real uncertainty. Keep closely related hypotheses in the same context. Reserve review for a concrete risk or an actual project requirement.

## Performance work

Capture a baseline on the real workload. Identify the limiting resource, measurement boundary, cache state, and whether the intended work actually ran. Make one change, rerun under comparable conditions, and check correctness. Report variability and tradeoffs. Use an installed benchmark-checklist skill before reporting measured performance when available.

For an explicitly requested hillclimb, name the metric, workload, stopping condition, and user budget. Keep a short record of hypotheses and outcomes. Stop after two consecutive trials without useful improvement unless new evidence supports another attempt, or the user specified a different run length. Do not turn a one-off optimization into an indefinite search.

Finish with the cause supported by evidence, the changed behavior, and the checks performed. A suspected cause remains labeled as suspected.
