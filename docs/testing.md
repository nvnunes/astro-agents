# Testing

This is the source of truth for validation in `astro-agents`. Apply every row
that matches the change, deduplicate checks, and read only the relevant linked
guidance. Commands run from the project root; use the
[repository environment](testing/agent-surface.md#environment-and-deterministic-checks).

## Change-To-Check Routing

| Change Or Task | Required Checks And Timing | Guidance |
|---|---|---|
| `AGENTS.md`, `SKILL.md`, skill references/scripts, or agent-facing prompts | Deterministic harness and agent-surface review before completion. Review changed instructions, affected contracts, and necessary consumers. | [Harness](testing/agent-surface.md#environment-and-deterministic-checks) and [review scope](testing/agent-surface.md#review-scope) |
| Skill names, descriptions, `agents/openai.yaml`, discovery layout, or downstream usage guidance | Runtime discovery smoke test before completion, in addition to applicable surface checks. | [Discovery](testing/agent-surface.md#codex-runtime-discovery) |
| Documentation organization, ownership, profile, or completeness | Documentation-surface review before completion; use its profile chooser. | [Review scope](testing/agent-surface.md#review-scope) |
| Documentation-surface, code-quality, or upgrade-review behavior | Harness plus the corresponding specialized review, alongside applicable agent-surface checks. | [Specialized review requirements](testing/agent-surface.md#specialized-review-requirements) |
| Research-logging tool or contract | Applicable focused checks while iterating; complete tool gate before any tool change is complete. | [Focused checks](testing/research-logging.md#focused-contract-and-validator-checks), [complete gate](testing/research-logging.md#complete-tool-gate), and [reproduction checks](testing/research-logging.md#reproduction-checks) |
| Research-log command/input-registry guidance affecting `pyrun` | Focused `pyrun` test before completion; guidance-only edits do not require the complete tool gate. | [Focused checks](testing/research-logging.md#focused-contract-and-validator-checks) |
| Skill-selection evaluation explicitly chosen for a relevant selection change | Optional model evaluation after the deterministic fixture/discovery checks. | [Selection evaluation](testing/agent-surface.md#skill-selection-eval-cases) |
| Agent behavior evaluation explicitly chosen | Optional diagnostic workflow; run its cheap compatibility check before model trials. | [Behavior evaluations](testing/agent-behavior-evaluations.md) |

All changes require the [whitespace check](testing/agent-surface.md#environment-and-deterministic-checks).
A prose-only relocation of testing instructions does not trigger the research
tool suite. A status-only plan update uses existing evidence; it does not
trigger implementation tests or technical reviews again.

## Completion Scope

A required gate failure blocks completion of the change it covers. Continue
independent work only when it does not depend on the failed condition. Record
which checks failed and which were not run; neither is passing evidence.

Reuse successful checks and review conclusions while the covered state remains
applicable. Repeat affected checks after relevant changes or failures, not
merely because a status changed or another command ran. Resolve overlapping
review findings once. Tests do not replace required reviews, and shared
reviews do not replace project checks.

Model evaluations are optional and comparatively expensive; they are not
routine completion gates. Discovery and fixture validation establish
structural correctness, not reliable model selection or measured efficiency.

For adoption in another project, adapt
[the downstream testing example](../examples/downstream-testing.md) with that
project's commands and completion requirements.
