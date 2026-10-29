# Braces

A compact Codex workflow adapted from Poteto/pstack. Braces keeps focused implementation and real verification while removing automatic agent panels and repeated workflow setup.

Ordinary tasks stay in the main chat. One helper is the default maximum for an entire task, including fixes and delivery. Helpers cannot spawn more agents. An explicit request for a larger team can override that limit.

## What changes

| Decision | Braces default |
| --- | --- |
| Feature, bug fix, refactor, explanation | Complete it in the current chat |
| Function or module design | Sketch the design inline |
| Subagents | Zero by default; one only for a concrete need |
| Follow-up fixes | Reuse the helper once, then continue locally |
| Comment, prose, and final review | Combine into the main agent's review |
| Independent review | Use when required or justified by a specific unresolved risk |
| Models | Keep the current model; preserve configured Sol/Terra and reasoning preferences |
| Specialist skills | Load only for the requested capability |
| Verification | Run relevant checks and honor project delivery requirements |

The limit is an instruction to the agent, not a runtime quota or billing control. Larger context, long builds, research, and difficult debugging can still use substantial resources. No measured token or cost reduction is claimed.

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

The reusable instructions are in [SKILL.md](skills/braces/SKILL.md). The skill has no dependency on another skill, a plugin, or an API key.

Run the repository check with Python 3.10 or newer:

```sh
python tools/check.py
```

This checks the package and caps the loaded instruction file at 1,250 whitespace-delimited words. It does not prove agent behavior. Use the [behavior scenarios](evals/scenarios.md) when changing delegation or verification rules.

For a usage comparison, run the same representative tasks from the same starting state with the same model, effort, tools, and acceptance checks. Count all descendant work. Report completion quality as well as usage, and distinguish recorded tokens from billing. An instruction-size comparison alone is not a usage benchmark.

See [attribution](ATTRIBUTION.md) and the [MIT license](LICENSE).
