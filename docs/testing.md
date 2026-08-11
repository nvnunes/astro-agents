# Testing

This document is the human-facing source of truth for validation requirements in `astro-agents`.

This project is primarily a skills and documentation system, so validation combines automated agent-surface checks, Codex runtime discovery smoke tests, and review-driven checks rather than application-code tests.

## Minimum Validation

The example downstream testing document lives in `examples/downstream-testing.md`.

For this project, use the local validation rules in this document as the source of truth.

Use these user-facing review and planning skills for manual or review-driven validation:

- `$agent-surface-review`
- `$documentation-surface-review`
- `$code-quality-review`
- `$project-upgrade-planning`

Review outputs that combine multiple internal references should include a short `Review Path Summary` showing the active skill and material references used.

## Automated Validation

Run the deterministic repository-local harness before treating skill-surface changes as complete:

```bash
python3 scripts/validate_agent_surface.py
```

This checks:

- `SKILL.md` frontmatter integrity, name and directory alignment, nonempty descriptions, and duplicate names
- `agents/openai.yaml` presence and basic metadata alignment for each user-facing skill
- package-internal skill references and direct sibling-skill references
- activation eval fixture shape in `tests/activation_cases.csv`
- retired prompt-family paths, deleted skill names, deleted shared-reference paths, and deleted source-of-truth document paths

Also run:

```bash
git diff --check
```

When changing `skills/research-logging/scripts/pyrun` or research-log command/data-index guidance that affects `pyrun`, also run:

```bash
python3 skills/research-logging/tests/test_pyrun.py
```

When changing research-log section classification, evidence presentation,
validation behavior, or `research_log_validation.py`, also run:

```bash
python3 -m unittest skills/research-logging/tests/test_research_log_validation.py
```

Use `skills/research-logging/tests/presented-evidence-cases.md` as the focused
manual behavior cases for record, summarize, review, and validation changes.

## Codex Runtime Discovery

Run the Codex runtime discovery smoke test when changing skill names, skill descriptions, `agents/openai.yaml`, the user-level skill layout, or downstream usage guidance:

```bash
python3 scripts/validate_agent_surface.py --codex-discovery
```

This expects the local user-level symlink:

```text
$HOME/.agents/skills/astro-agents -> <astro-agents-path>/skills
```

The discovery check runs `codex debug prompt-input` and asserts that every current `astro-agents` skill appears in the model-visible skill list with the expected name and file path. Treat this as a hard local Codex discovery smoke test, not as proof that implicit activation will always choose the intended skill.

## Activation Eval Cases

Use `tests/activation_cases.csv` as the maintained activation eval fixture.

The fixture includes:

- explicit `$skill-name` prompts
- implicit natural-language prompts
- negative near-miss prompts for neighboring skills

Every user-facing skill should have at least one explicit positive case, one implicit positive case, and one negative exclusion case. The deterministic harness enforces that coverage shape.

Implicit activation is model-mediated. The fixture gives stable prompts and exact expected selected skills for repeated manual or scripted eval runs, but it is not a deterministic unit test of model choice.

Run the optional activation eval runner only when activation behavior might change:

```bash
python3 scripts/validate_agent_surface.py --activation-eval
```

This runs each fixture row through `codex exec` in ephemeral read-only mode and asks Codex for a compact JSON activation decision. Because it starts a model turn per fixture row, the full activation eval is comparatively expensive and should not be part of routine validation.

Run it for changes to:

- `SKILL.md` names or descriptions
- activation boundaries between neighboring skills
- `tests/activation_cases.csv`
- user-level skill layout or Codex discovery assumptions
- major `AGENTS.md` or runtime-context changes that could affect skill selection

Do not run the full activation eval for routine documentation edits, reference-file cleanup, or stale-path-only changes unless those edits affect activation behavior. Treat failures as activation-regression signals that need human review, not as proof that the skill can never work.

## Agent Surface Validation

- Changes to `AGENTS.md` files:
  - run `python3 scripts/validate_agent_surface.py`
  - run `$agent-surface-review`

- Changes to `SKILL.md`, skill references, or skill scripts:
  - run `python3 scripts/validate_agent_surface.py`
  - run `python3 scripts/validate_agent_surface.py --codex-discovery` when names, descriptions, metadata, or discovery layout change
  - run `python3 scripts/validate_agent_surface.py --activation-eval` only when activation descriptions, skill names, discovery assumptions, or neighboring skill boundaries change
  - run `$agent-surface-review`
  - include focused prompt-writing, scope, or activation-boundary checks when the changed skill affects those behaviors

- Changes to documentation-surface review behavior:
  - run `python3 scripts/validate_agent_surface.py`
  - run `$documentation-surface-review`

- Changes to code-quality review behavior:
  - run `python3 scripts/validate_agent_surface.py`
  - run `$code-quality-review`

- Changes to upgrade planning behavior:
  - run `python3 scripts/validate_agent_surface.py`
  - run `$project-upgrade-planning`
