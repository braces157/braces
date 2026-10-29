# Specialist routing

Use this map when the task needs a capability beyond its Braces playbook. The names below are optional installed skills, not dependencies. Find the exact skill in the runtime catalog and read its instructions before using it. Do not assume a name supplies tools or permissions.

Read only the relevant row. Prefer one specialist. An extra skill should supply a distinct capability, not another generic workflow wrapper.

| Concrete need | Candidate skills | Use and boundary |
| --- | --- | --- |
| Exact app operation or real UI verification | Project verification skill; computer-use; create-verification-skill | Use an existing driver first. Create one only when repeated verification justifies it. |
| Website, app styling, or visual redesign | sites; redesign-existing-projects; design-taste-frontend; minimalist-ui; industrial-brutalist-ui; high-end-visual-design | Pick the platform-required tool and one relevant visual direction. Do not stack competing style systems. |
| A real architecture comparison or competing implementations | architect; arena | Use on explicit request or a consequential unresolved design choice. Ordinary boundaries stay in the design playbook. |
| Security, concurrency, or deep maintainability concerns | blast-radius; interrogate; thermo-nuclear-review; thermo-nuclear-code-quality-review; thermos | Start with the exact concern. A whole panel needs distinct questions and an adequate budget. |
| Hard invariants beyond practical test coverage | create-formal-verification-skill; maintain-formal-verification-skill | Use for a concrete property and suitable model, not every stateful function. |
| A regression with a useful local test | tdd | Use when requested or when the reproduction has an affordable test target. |
| Measured speed or resource usage | benchmark-checklist | Apply before reporting performance. Keep correctness and comparable workloads in the evidence. |
| PR-specific repair or preparation | fix-ci; get-pr-comments; fix-merge-conflicts; make-pr-easy-to-review | Load the one matching the actual problem. Use Braces' review playbook to coordinate the whole PR. |
| Long-running PR supervision | babysit | Prefer Braces' monitoring policy. Use the specialist when its service mechanics are needed or it is explicitly requested. |
| Complex explanation, rationale, or historical reconstruction | how; why; teach; recall | Basic questions stay in the investigate playbook. Source-specific techniques may help large questions; mandatory fan-out still needs a budget. |
| Exhaustive parallel coverage | swarm | Partition genuinely independent work. Do not turn every checklist row into a worker. |
| Ambitious migration or unattended program | figure-it-out; show-me-your-work | Use when a custom plan or reviewable decision trail adds value. Keep the original task budget across phases. |
| Durable preference or skill changes | skill-creator; automate-me; reflect; correct | Use the narrow authoring skill first. Reflection panels and tooling changes need the corresponding request or demonstrated need. |
| Format-specific artifacts or services | documents; pdf; presentations; spreadsheets; imagegen; plugin-creator | Use the artifact or service skill for its real mechanics and verification requirements. |
| Language, documentation, or OpenAI-specific guidance | typescript-best-practices; technical-writing; unslop; openai-docs | Apply relevant conventions or current product guidance without turning each into an agent assignment. |

## Keep coordination in Braces

Loading a skill and spawning an agent are separate decisions. Many skills can supply techniques within the current chat. A skill that mandates multiple agents is not a cheap single-agent technique.

For optional use, inspect mandatory dispatch and review requirements first. Choose that workflow only if it fits the task's remaining budget and parent-owned dispatch. Otherwise use the self-contained Braces playbook and report any meaningful capability gap. Do not describe this fallback as a completed specialist run.

For explicit use, honor the requested workflow and its real requirements. Determine the smallest valid team from that skill, count it against the explicitly requested workflow budget, and dispatch from the parent. Host restrictions and a tighter explicit user budget still apply. Explain an unsatisfied requirement precisely; do not silently reduce a required panel or invent independent approval.

If a named specialist is missing, use a Braces fallback when it can meet the outcome. Report the missing skill when it materially limits the result. Do not install extra skills or plugins just to fill this table. They remain optional until the user authorizes installation or the task already covers it.
