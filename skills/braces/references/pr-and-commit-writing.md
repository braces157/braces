# PR and commit writing

Write for a reviewer who knows the project but has not seen the conversation. Explain the reason and observable result before implementation detail. Apply this guide in the current chat; writing does not need a separate agent.

## Ground the text

Read the repository's contribution rules, PR template, and recent message conventions. Use the actual staged diff for a commit. Use the full branch diff against the PR's base for the PR, not just its latest commit. Gather the original problem, final behavior, relevant checks and outcomes, and any material compatibility or rollout constraint.

Treat old descriptions and plans as context to verify. Rewrite the title and body when scope changes. Preserve applicable template fields and issue links, but replace stale claims. Do not invent issue numbers, causes, test results, performance gains, or proof of safety.

## Titles and commit subjects

Follow the repository convention. Otherwise use `type(scope): imperative subject`, with scope only when it clarifies the affected area. Common types are `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, and `chore`. Choose the type from what changed; do not label behavior changes as refactoring or cleanup.

Name the concrete result. Prefer about 72 characters or fewer when that preserves meaning. Use sentence case, a plain verb, and no trailing period. Name a symbol only when it makes the change clearer. Avoid vague titles such as "Improve handling", "Various fixes", or "Update files".

Examples are hypothetical, not claims about this repository:

| Weak | Specific |
| --- | --- |
| Fix queue bug | `fix(queue): preserve pending jobs after a timeout` |
| Improve settings | `feat(settings): add a reset-to-defaults action` |
| Clean up parser | `refactor(parser): share timestamp parsing across adapters` |
| Update docs | `docs(setup): document the Windows SDK requirement` |

The PR title describes the combined outcome. Individual commit subjects describe their own staged changes; do not copy the PR title onto every commit.

## PR descriptions

Use the repository template first. Otherwise, a small change needs one or two short sentences plus relevant verification. A substantive change uses these sections, normally fitting on one screen:

- `## Why`: state the concrete problem or missing capability and resulting behavior in one to three sentences. Include a trigger and before/after example when helpful.
- `## What changed`: one to three bullets for the changes a reviewer must understand. Describe both sides of a rename or changed contract. Avoid a file inventory.
- `## Scope and risk`: identify affected users or callers and a real compatibility, migration, rollout, or coverage concern. Mention exclusions only when they prevent a likely misunderstanding. Skip this section if it adds no useful information.
- `## Verification`: give actual commands or user paths and their observed outcomes. Distinguish passed, failed, pending, and not run. State a relevant unperformed check and its reason.

Add tradeoffs or a reviewer starting point only when they explain a consequential choice or difficult diff. Prefer short prose over checklist filler. Link detailed evidence rather than embedding logs. For UI claims, include a screenshot or recording when available and useful. Keep performance claims tied to measured evidence.

For example, given evidence that a queue fetch timeout discarded pending jobs and the named regression test passed:

```md
## Why
When a queue fetch timed out, pending jobs disappeared before the retry.
Keep those jobs queued so the retry can resume them.

## What changed

- Remove jobs only after the server acknowledges them.

## Verification

- `pytest tests/test_queue.py -k timeout`: passed.
```

Do not include agent rosters, workflow recaps, full commit hashes, abandoned approaches, generic "low risk" claims, or repeated "no changes" statements. Keep implementation details that explain behavior or help assess risk. Write a body that will still make sense if used as the squash commit message.

## Commit bodies

Omit the body when the subject fully explains a straightforward change. Otherwise add a blank line, then explain why the change was needed and the non-obvious constraint or behavior. Do not restate the subject or paste the PR template into every commit. Use repository-required footers. Include verification only when it matters to understanding that commit.

```text
fix(queue): preserve pending jobs after a timeout

A failed fetch cleared pending jobs before a retry could read them.
Retain each job until the server acknowledges it so retries can resume.
```

Keep separable changes in separate commits when practical. A new message must not describe unstaged work, later commits, or planned tests. Improving prose alone does not authorize rebasing or force-pushing published history.

## Final pass

Compare every claim with the final diff and check results. Read the title and opening paragraph alone: they should explain the change without the chat. Cut duplicate facts, generic praise, and details already obvious from the diff. Check that formatting survives publication and that the description still matches the current revision.
