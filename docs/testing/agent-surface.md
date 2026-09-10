# Agent-Surface Checks

Use this reference for the checks selected by [Testing](../testing.md).
Commands run from the project root.

## Environment And Deterministic Checks

Create the repository-local Conda environment once, and refresh it when
`environment.yml` changes:

```bash
conda env create --prefix ./.conda --file environment.yml
conda env update --prefix ./.conda --file environment.yml --prune
```

Run the deterministic repository-local harness before treating skill-surface changes as complete:

```bash
./.conda/bin/python scripts/validate_agent_surface.py
```

This checks:

- `SKILL.md` frontmatter integrity, name and directory alignment, nonempty descriptions, and duplicate names
- `agents/openai.yaml` presence and basic metadata alignment for each user-facing skill
- package-internal skill references and direct sibling-skill references
- skill-selection fixture shape in `tests/skill_selection_cases.csv`
- retired prompt-family paths, deleted skill names, deleted shared-reference paths, and deleted source-of-truth document paths

Also run:

```bash
git diff --check
```

## Codex Runtime Discovery

Run the Codex runtime discovery smoke test when changing skill names, skill descriptions, `agents/openai.yaml`, the user-level skill layout, or downstream skill-discovery guidance:

```bash
./.conda/bin/python scripts/validate_agent_surface.py --codex-discovery
```

This expects the local user-level symlink:

```text
$HOME/.agents/skills/astro-agents -> <astro-agents-path>/skills
```

The discovery check runs `codex debug prompt-input` and asserts that every
current `astro-agents` skill appears in the model-visible skill list with the
expected name and source path, either directly or through a declared skill-root
alias. Treat this as a hard local Codex discovery smoke test, not as proof that
the model will select the intended skill for every implicit prompt.

## Skill-Selection Eval Cases

Use `tests/skill_selection_cases.csv` as the maintained skill-selection eval
fixture.

The fixture includes:

- explicit `$skill-name` prompts
- implicit natural-language prompts
- negative near-miss prompts for neighboring skills

Every user-facing skill should have at least one explicit positive case, one implicit positive case, and one negative exclusion case. The deterministic harness enforces that coverage shape.

Implicit skill selection is model-mediated. The fixture gives stable prompts
and exact expected selected skills for repeated manual or scripted eval runs,
but it is not a deterministic unit test of model choice.

Run the optional skill-selection eval only when selection behavior might
change:

```bash
./.conda/bin/python scripts/validate_agent_surface.py --skill-selection-eval
```

This runs each fixture row through `codex exec` in ephemeral read-only mode and
asks Codex for a compact JSON selection decision. Because it starts a model
turn per fixture row, the full skill-selection eval is comparatively expensive
and should not be part of routine validation.

When this optional evaluation is selected, use it for changes to:

- `SKILL.md` names or descriptions
- selection boundaries between neighboring skills
- `tests/skill_selection_cases.csv`
- user-level skill layout or Codex discovery assumptions
- major `AGENTS.md` or runtime-context changes that could affect skill selection

Do not run the full skill-selection eval for routine documentation edits,
reference-file cleanup, or stale-path-only changes unless those edits affect
selection behavior. Treat failures as selection-regression signals that need
human review, not as proof that the skill can never work.
