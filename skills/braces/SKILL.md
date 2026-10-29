---
name: braces
description: Coordinate development with selective playbooks, specialist skills, and task-sized agent budgets. Use when the user requests Braces, asks for a usage-conscious coding workflow, or configures Braces as their default working style.
---

# Braces

Complete the task with the smallest process that resolves its actual uncertainty. Keep project requirements and observed verification. Work in the current chat whenever delegation would add more coordination than useful work.

## Choose the route

Read applicable project instructions and enough source to identify the result and completion check. Handle a small, clear task directly. For substantive work, read only the matching playbook. Read another only when the task enters that kind of work.

| Work | Playbook |
| --- | --- |
| Feature, refactor, migration, or sustained implementation | [Build](playbooks/build.md) |
| Bug, failing test, runtime symptom, or performance problem | [Debug](playbooks/debug.md) |
| Uncertain architecture, UI interaction, or competing approaches | [Design](playbooks/design.md) |
| Explanation, research, source investigation, or session recall | [Investigate](playbooks/investigate.md) |
| Review, CI, PR preparation, babysitting, or release | [Review and deliver](playbooks/review-and-deliver.md) |

These playbooks work without pstack. Use [specialist routing](references/specialists.md) when a requested capability would benefit from an installed specialist. Read its relevant row, then that skill; do not load the catalog of skills it mentions. Select one primary specialist and add another only for a distinct need.

Do not automatically activate poteto-mode as a second coordinator. Function boundaries, PR creation, and words such as "important" or "thorough" do not trigger panels. Explicit workflow requests retain their meaning. If the user requests arena, do not silently replace it with your own single-pass comparison.

## Size the task before delegating

These budgets permit delegation when a concrete benefit justifies it; they do not require filling slots. They never override host restrictions or a tighter user budget.

| Class | Evidence | Total helpers | Active helpers |
| --- | --- | ---: | ---: |
| Small | Clear local change or bounded explanation | 0 | 0 |
| Standard | One coherent feature, defect, or investigation | Up to 2 | 1 |
| Complex | Multiple independent deliverables, a migration with separate validation stages, or distinct substantial uncertainties | Up to 4 | 2 |
| Explicit team | User requests a team or named multi-agent workflow | Smallest team that satisfies it | Within host capacity |

Most standard work still uses zero helpers. File count and perceived importance do not determine complexity. Before the first spawn, state the class, ceiling, and each planned role's concrete benefit. Reserve a slot for independent review when it is required. An implementation agent cannot independently approve its own work.

The budget covers implementation, correction, review, and delivery. Count all distinct helper contexts, including replacements and agents started by routed workflows. Do not reset it at a phase change, compaction, or related follow-up. If newly discovered work changes the class, explain that evidence before increasing the ceiling; allow at most one such increase, up to the complex ceiling. Budget exhaustion alone is not evidence. Beyond that, continue locally or seek an explicit expansion only if needed.

## Coordinate useful work

Delegate a bounded implementation with clear ownership, a substantial independent investigation, or a focused independent review. Routine status, prose cleanup, comment review, and reassurance stay in the main chat. Use different models only when the perspective matters, not to create a panel by habit.

The parent owns every dispatch. Helpers must not spawn agents or activate delegation workflows. Enforce this through the handoff even when a specialist normally delegates. Optional specialists must fit the budget and this coordination model; otherwise use the matching Braces playbook. For an explicit workflow whose mandatory steps conflict with user or host limits, explain the conflict and finish independent work without claiming the workflow completed.

Use minimal context when supported. Supply the goal, paths, decisions, owned files, acceptance check, budget, and no-delegation rule. Tell writers that others share the workspace and that they must preserve others' changes. Isolate overlapping writes, or sequence them. Keep agent reports compact with findings, files, checks, and blockers.

Continue independent work without duplicating the helper's assigned investigation. Inspect its output and verify integrated behavior. Reuse a helper for a related correction when supported. After one focused follow-up, take over or report the remaining issue. Do not retry capacity failures under other models. Never replace a required independent review with self-approval.

## Models and context

Use the current model for work you can complete yourself. Do not spawn merely to switch models or claim to change the parent model. Honor configured routing and use only runtime-supported models and effort levels.

Absent an override, prefer Sol for coding, debugging, unfamiliar-source exploration, and synthesis. Prefer Terra for established explanations, visual inspection, and defined computer-use work. Use high reasoning by default. Sol extra high is the first escalation for difficult unresolved reasoning. Astra high is an alternative when a broader perspective has a concrete purpose. Escalation consumes the same agent budget. Disclose unavailable preferences and fallbacks; do not lower an explicit effort preference automatically.

Search before reading. Bound output, batch independent reads, and reuse established evidence. Keep edits and dependent actions sequential. Summarize large logs locally. Read history only when relevant, beginning with summaries and checking raw evidence for disputed claims.

For long work, keep a short state note with scope, decisions, completed checks, remaining work, and agents used against the budget. Do not copy entire playbooks or require audit-file reviews. When two attempts share a failure, test their common assumption. Repeat a successful check only after relevant changes or stale evidence.

## Completion

Exercise the changed behavior and run required project checks. Preserve delivery requirements such as release builds, installation, PATH checks, and UI inspection. Combine routine correctness, comment, and prose cleanup into one final diff review. Add independent review only when required or justified by a specific unresolved risk.

Complete authorized work without repeatedly asking permission. This skill grants no additional authority to publish, deploy, delete, merge, or send messages. Report the outcome, checks, and material limitations concisely. Distinguish an unperformed check from a pass. Report budget exceptions; never infer token or cost savings from instruction length or agent counts.
