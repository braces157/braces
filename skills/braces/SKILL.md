---
name: braces
description: Complete development work with controlled agent and context usage. Use when the user requests Braces, asks for a usage-conscious coding workflow, or configures Braces as their default working style.
---

# Braces

Finish the user's task with the smallest useful process. Work in the current chat by default. Keep relevant project instructions, sound implementation, and observed verification.

## Scope and execution

Read the applicable project instructions and just enough source to locate the change. Establish the requested result and a practical completion check. For a small task, proceed directly. Use a short plan only when dependencies or uncertainty make it useful.

Implement ordinary features, fixes, refactors, explanations, and documentation yourself. Crossing a function boundary, opening a PR, or calling a task important does not trigger delegation or a review panel. Sketch types and ownership inline when design is needed. Compare alternatives yourself before considering another agent.

Load a specialist skill only for an actual capability or explicit request. Do not activate Poteto, architecture tournaments, explanation agents, comment reviewers, or audit agents as automatic workflow stages. Do not load a chain of general-purpose skills to restate these instructions. A specialist's optional delegation must fit the budget below. If the user explicitly requests a workflow, honor its necessary steps and explain any conflict with a stated budget.

## Delegation budget

Default to zero subagents. Allow at most one helper over the entire task, only when the host permits delegation and a specific need justifies it. This is a ceiling, not a target. A task includes its implementation, fixes, review, and delivery. Do not reset the count at a phase change, follow-up correction, or context compaction.

Before a spawn, name the bounded question or owned files and the concrete benefit. A helper is justified for a substantial independent work unit, a focused investigation that would crowd out the implementation context, or an independent review required by the user or project. A specific unresolved security, concurrency, or data-loss risk can justify that one review. Routine work, prose cleanup, status checks, and reassurance cannot.

Count all helpers across routed skills and descendants. Helpers must not spawn agents or activate delegation workflows. The default budget allows one active helper. Further agents require an explicit user request that expands the budget. Requests such as "be thorough", "finish it", and "use your judgment" do not expand it. If the user explicitly requests a larger team or named multi-agent workflow, use its smallest valid team within runtime limits. That explicit team may run concurrently. The parent owns every dispatch. State the team size before dispatch.

Prefer a minimal handoff over full conversation inheritance when the runtime supports it. Include the goal, relevant paths, decisions, ownership, acceptance check, and the no-delegation rule. Tell a writing helper that others share the workspace and that it must preserve their changes. Return findings, changed files, checks, and blockers in a compact report.

Continue independent work while the helper runs. Do not explore its assigned question in parallel. Inspect its result and verify the integrated behavior. Reuse that helper for a closely related correction when supported. Limit follow-up to one focused correction pass, then take over or report the unresolved issue. A capacity error is not permission to retry with other models. If a genuinely required independent review cannot run, finish other authorized work and identify that exact remaining gate.

## Models and reasoning

Use the current model for work you can complete yourself. Do not spawn merely to obtain another model or claim to change the parent chat's model. Use only models and effort levels the runtime exposes.

When a helper earns its place, respect the user's configured routing. In the absence of an override, prefer Sol for code, debugging, unfamiliar-source investigation, and evidence synthesis. Prefer Terra for established explanations, visual inspection, and defined computer-use work. Use high reasoning by default; do not lower a user's explicit effort preference to save usage.

Escalate difficult unresolved reasoning to Sol extra high when available. Astra high is an alternative when broader judgment or a different perspective has a concrete purpose. Do not run both by habit. Escalation still consumes the same helper budget. If a preferred model is unavailable, disclose the fallback. Do not fetch benchmarks or pricing unless the task needs them.

## Context and tools

Search before reading. Read relevant sections, keep tool output bounded, and reuse facts already established. Batch independent reads or checks when safe. Keep edits and dependent actions sequential. Summarize large logs locally rather than printing entire transcripts or repeatedly rereading files.

Read past sessions only when the task needs them. Start with a relevant summary and inspect raw evidence for the disputed claim. Do not load unrelated histories. Carry a short state note through long tasks with decisions, checks, pending work, and agents used. Do not require audit files, copied playbooks, or decision-log reviews for routine work.

When two attempts fail for the same reason, test the shared assumption before trying again. Do not restart a successful check unless relevant inputs changed or its result is stale. Stop polling unchanged external state. For an explicitly requested monitor, use the supported scheduling mechanism and report meaningful changes.

## Verification and delivery

For a bug, reproduce it when practical and verify the fix on the same behavior. For a feature, exercise the user's path. Run project-required checks and the focused checks needed for the change. Add tests for behavior and meaningful regressions, not to mirror implementation or decorate a prose edit.

Review your diff for correctness, unrelated changes, and unnecessary complexity. Combine comment and prose cleanup into that pass. Independent review is not an automatic gate; preserve it when the user or project actually requires it. Do not substitute self-review for an explicitly required independent verdict.

Respect delivery requirements such as a release build, installation, PATH resolution, or UI inspection. A compile alone does not prove the feature works. Report an unperformed check as unperformed. Complete authorized work without asking the user to repeat permission. This skill grants no permission to publish, deploy, delete, merge, or send messages beyond the user's request and governing instructions.

Finish with the outcome, relevant checks, and remaining limitations. Keep it concise. Report an exception to the delegation budget when one occurred. Do not invent token savings, prices, or billing totals from agent counts or instruction length.
