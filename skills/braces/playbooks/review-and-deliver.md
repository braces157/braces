# Review and deliver

Use for code review, CI repair, PR preparation, babysitting, and authorized release work. Select only the stages the user requested. Opening a PR does not itself request monitoring or merging.

## Review the relevant change

Read the diff, necessary surrounding code, applicable requirements, and current verification evidence. Inspect correctness, compatibility, security risks, and unnecessary complexity in one pass. Combine comment and prose cleanup with that pass. A small diff does not need separate style, comment, correctness, final, and evidence auditors.

Use one independent reviewer when required or when a specific unresolved risk earns it. Give it the current revision, scope, acceptance criteria, and known checks. Count it in the original task budget. A worker can review code it did not author, but cannot provide independent approval for its own changes. For a mixed diff, label the coverage and keep self-authored sections outside its independent verdict.

Triage each human or automated finding as actionable, unsupported, or needing a decision, using code and behavior as evidence. Fix accepted findings together when safe. Recheck affected behavior and request a focused follow-up from the same reviewer if needed. Passing CI does not substitute for an explicitly required independent review.

## Prepare a PR

Inspect repository status and preserve unrelated work. Follow the repository's contribution and branch rules. Include only intended files. Write a description of the problem, resulting behavior, relevant validation, and remaining limits. Use the template when present. Attach the created PR in hosts that support task attachments.

Verify remote state after publication. Report an unavailable check rather than declaring a PR merge-ready. Publication and merging require authorization from the task or standing instructions.

## Babysit deliberately

For a status question, inspect once and report. For an explicit babysitting request, use the user's endpoint; absent another endpoint, stop at merge-ready without merging. Fix in-scope failures and actionable review comments, then check the new revision.

Use one existing watcher or a supported scheduling tool for ongoing monitoring. Do not start duplicate watchers or poll while an unchanged run is still pending. Let a watcher wait with backoff and notify on meaningful change. Reuse it across fix rounds when possible. If persistent monitoring is unavailable, report current state and the limitation. A green result applies to its checked revision, not to later commits.

## Release and install

For authorized shipping, confirm the target revision, required checks, approvals, and requested action. For a dependent stack, verify and land only the eligible contiguous sequence from its base. If a gate is missing, continue independent preparation and identify it exactly.

Honor project release-build, installation, and launch-path requirements. Verify the artifact or executable the user actually receives. Do not claim an installed update from a source-only test. Report the published artifact or PR, verification, and any remaining user action.
