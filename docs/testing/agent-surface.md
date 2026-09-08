# Agent-Surface Validation

Use this reference for the gates selected by [Testing](../testing.md).
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

Run the Codex runtime discovery smoke test when changing skill names, skill descriptions, `agents/openai.yaml`, the user-level skill layout, or downstream usage guidance:

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

## Review Scope

Use `$agent-surface-review` for changed agent instructions and their affected
contracts. Select scope before loading review references. A bounded change
uses the focused route; an explicitly requested full review uses the full
workflow within its requested target. Load the documentation profile workflow
when organization, ownership, profile, or completeness is affected, or when
full review is requested. A routine operation-reference edit does not by
itself require documentation-architecture review.

Use `$documentation-surface-review` directly for documentation-only review.
Its chooser selects the applicable profile; it defaults to `private-default`
when the project does not declare another profile. Technical reviews are
required for the changed scope, not a reason to inspect unrelated documents.

When a review combines material references, include a short `Review Path
Summary` naming those sources. Reuse applicable review evidence and resolve
overlapping findings once.

## Specialized Review Requirements

Changes to review behavior require the deterministic harness and the relevant
review in addition to any applicable agent-surface review:

- Documentation-surface review behavior: `$documentation-surface-review`.
- Code-quality review behavior: `$code-quality-review`.
- Upgrade planning behavior: `$project-upgrade-planning`.

Include focused prompt-writing, scope, or selection-boundary checks when the
changed skill affects those behaviors. These reviews gate the changed behavior;
they do not require unrelated source-code, documentation, or upgrade work.
