# Braces

A selective development coordinator for Codex, adapted from Poteto/pstack. Braces combines focused playbooks with access to installed specialist skills, while controlling agent and context usage.

Ordinary work stays in the main chat. Larger tasks can use a small team when the work is separable or an independent review is justified. Each task gets a budget that covers implementation, fixes, review, and delivery. Helpers cannot spawn more agents.

## Task budgets

| Task | Total helpers | Active helpers |
| --- | ---: | ---: |
| Small, clear change or explanation | 0 | 0 |
| Standard feature, bug, or investigation | Up to 2 | 1 |
| Complex migration or multiple independent work units | Up to 4 | 2 |
| Explicitly requested team or multi-agent workflow | Smallest team satisfying the request | Within host capacity |

These are ceilings, not staffing targets. A standard feature can still use zero helpers. File count and importance alone do not justify a larger team. Reserve capacity for required independent review. New evidence can justify one class increase, while keeping agents already used in the count. User budgets and host restrictions take precedence.

## Playbooks and specialist skills

Braces loads only the playbook relevant to the current work:

- [Build](skills/braces/playbooks/build.md) covers features, refactoring, and migrations.
- [Debug](skills/braces/playbooks/debug.md) covers reproduction, root causes, CI diagnosis, and performance experiments.
- [Design](skills/braces/playbooks/design.md) covers architecture choices, prototypes, and real UI verification.
- [Investigate](skills/braces/playbooks/investigate.md) covers explanations, evidence gathering, and session recall.
- [Review and deliver](skills/braces/playbooks/review-and-deliver.md) covers review, PRs, babysitting, and authorized releases.

The [specialist map](skills/braces/references/specialists.md) connects concrete needs to optional installed skills such as architecture, arena, formal verification, UI design, document creation, and PR repair. Loading a specialist is a separate decision from spawning an agent. Braces retains control of the task budget and dispatches.

The playbooks work without pstack installed. If you have pstack, Braces can use its specialists selectively. It does not automatically invoke the Poteto coordinator. An explicit request for a specialist workflow retains that workflow's requirements; Braces reports an incompatible limit instead of claiming a partial run completed it.

## What changes

| Decision | Braces default |
| --- | --- |
| Feature, bug fix, refactor, explanation | Complete it in the current chat |
| Function or module design | Sketch inline; open the design playbook for a real uncertainty |
| Subagents | Use only justified roles within the task's budget |
| Follow-up fixes | Reuse a helper for one focused correction, then take over |
| Comment, prose, and final review | Combine into the main agent's review |
| Independent review | Use when required or justified by a specific unresolved risk |
| Models | Keep the current model; preserve configured Sol/Terra and reasoning preferences |
| Specialist skills | Load only for the requested capability |
| Verification | Run relevant checks and honor project delivery requirements |

Budgets are instructions to the agent, not runtime quotas or billing controls. Larger context, long builds, research, and difficult debugging can still use substantial resources. No measured token or cost reduction is claimed.

## Install

Clone this repository into a directory you choose:

```sh
git clone https://github.com/braces157/braces.git
```

Copy the `skills/braces` folder into a supported user skill directory, such as `~/.agents/skills/braces`. For a project-only installation, copy it to `.agents/skills/braces` inside that project. Keep only one discoverable copy. These locations follow the [official Codex customization guidance](https://learn.chatgpt.com/docs/customization/overview#skills).

For PowerShell, run this from the cloned repository. It stops if Braces is already installed there:

```powershell
$skillDestination = Join-Path $env:USERPROFILE '.agents/skills/braces'
if (Test-Path -LiteralPath $skillDestination) { throw 'Braces already exists at the destination.' }
New-Item -ItemType Directory -Force (Split-Path $skillDestination) | Out-Null
Copy-Item -LiteralPath './skills/braces' -Destination $skillDestination -Recurse
```

Start a new chat and invoke `$braces`, or ask Codex to use Braces.

## Make Braces your default

In the applicable `AGENTS.md`, replace the instruction that automatically loads Poteto with this block. Preserve unrelated project rules and model preferences.

```md
Use the braces skill for substantive development tasks.
Work in the current chat by default. Follow its task-wide delegation budget.
Do not automatically load poteto-mode or its panels alongside Braces.
Honor explicit workflow requests and project verification requirements.
```

Installing Braces alone does not remove an existing Poteto routing rule. Keeping both default routers active can reintroduce the overhead. Poteto can remain installed for explicit use.

To stop using Braces, remove its default routing block and its installed skill folder. Restore your previous routing instruction if needed.

## Maintain and evaluate

The coordinator is in [SKILL.md](skills/braces/SKILL.md). Copy the entire `skills/braces` folder when installing or updating, including its playbooks and references. The core workflow has no dependency on another skill, a plugin, or an API key. Optional specialists have their own requirements.

Run the repository check with Python 3.10 or newer:

```sh
python tools/check.py
```

This checks local links, keeps skill links inside the installable folder, and caps instruction size. Limits are 1,250 whitespace-delimited words for the coordinator, 650 per playbook, and 900 per reference. Most tasks load the coordinator and one playbook. Static checks do not prove agent behavior. Use the [behavior scenarios](evals/scenarios.md) when changing delegation or verification rules.

For a usage comparison, run the same representative tasks from the same starting state with the same model, effort, tools, and acceptance checks. Count all descendant work. Report completion quality as well as usage, and distinguish recorded tokens from billing. An instruction-size comparison alone is not a usage benchmark.

See [attribution](ATTRIBUTION.md) and the [MIT license](LICENSE).
