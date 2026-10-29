# Investigate

Use for source explanations, decision research, read-only audits, and recovery of past working context. Keep the deliverable aligned with the question; investigation alone does not authorize code or external changes.

## Scope the evidence

Identify the question, relevant project, time range when needed, and what evidence would answer it. Use the user's supplied context first. Search targeted paths or connected sources before opening large files. Stop expanding once the question is supported.

For how something works, follow the entry point, major state changes, and output. Explain that path in the current chat. A short source walkthrough does not need separate explorer and explainer agents.

For why a decision was made, distinguish code behavior from recorded intent. Search relevant commits, issues, documents, or conversations. Treat multiple summaries of the same source as one evidence chain. Missing documentation supports "not found in the sources checked", not a claim that a decision never existed.

For session recall, choose relevant chat summaries first. Read the specific raw turns needed to establish user preferences, actual tool activity, or a disputed claim. Check important claims against current files, branches, or issues. Preserve completed work and open questions without copying full transcripts into the main context.

Use authoritative sources for technical research and current documentation for changing facts. Mark inference and uncertainty. Keep private source material out of public deliverables unless the user authorized its disclosure.

## Delegate only a distinct investigation

A large independent evidence source or unfamiliar subsystem may justify a bounded investigator. Give it one question, source boundaries, and an evidence format. Its report should contain findings, citations or paths, and limits. Do not investigate the same assignment in parallel or automatically add a synthesis agent. The parent combines the findings.

If two sources disagree, inspect their dates, scope, and underlying evidence before seeking another opinion. Absence of a connector is a coverage limit, not a reason to invent a result.

Finish with the direct answer and evidence needed to assess it. State the scope checked and material uncertainty. Recommend a next action only when it follows from the findings; do not turn a concise answer into an unrequested implementation program.
