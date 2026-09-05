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

When changing `skills/research-logging/scripts/pyrun` or research-log
command/input-registry guidance that affects `pyrun`, also run:

```bash
./.conda/bin/python -m unittest skills/research-logging/tests/test_pyrun.py
```

When changing the persisted `pyrun-outputs.json` shape, also run its focused
contract tests:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest discover \
  -s skills/research-logging/tests -p 'test_research_log_pyrun_outputs.py'
```

When changing research-log section classification, evidence presentation, or
validation behavior, also run:

```bash
./.conda/bin/python -m unittest discover \
  -s skills/research-logging/tests -p 'test_research_log_validation*.py'
```

For output-code currentness or material-graph integration, use these focused
tests while iterating before running that complete validator set:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_research_log_validation_engine.py \
  skills/research-logging/tests/test_research_log_validation_material_graph.py
```

For any research-logging tool change, run the complete tool gate rather than
linting only the main validator:

```bash
./.conda/bin/python -m py_compile skills/research-logging/scripts/log \
  skills/research-logging/scripts/pyrun \
  skills/research-logging/scripts/pyrun_code_observer.py \
  skills/research-logging/scripts/research_log_data.py \
  skills/research-logging/scripts/log_commands/*.py \
  skills/research-logging/scripts/validation/*.py
./.conda/bin/ruff check skills/research-logging/scripts \
  skills/research-logging/tests
./.conda/bin/mypy
./.conda/bin/python scripts/check_research_logging_complexity.py
./.conda/bin/python -m unittest discover \
  -s skills/research-logging/tests -p 'test_*.py'
```

The pinned local Conda environment is the quality gate. Ambient Python, Ruff,
or mypy installations may be used for diagnosis, but not as completion
evidence for a research-logging tool change.

The complexity check is a ratchet over explicitly recorded complexity debt. It
allows refactoring to reduce findings, but rejects a new complex function, a
higher complexity score, or growth in the total advisory finding count.

### Research-Log Mechanical Validation Boundary

The normative behavior exercised by this gate is owned by
[`docs/research-log-mechanical-validator-spec.md`](research-log-mechanical-validator-spec.md).
Keep field grammars, schema versions, lifecycle rules, limits, and diagnostic
semantics there rather than restating them in this testing guide. When a
contract changes, update the specification and its tests; update this section
only when the gate or its coverage boundaries change.

The complete research-logging tool gate must cover:

- public CLI discovery, dispatch, authoring, reorganization, and validation
  operations defined by [Public Management And Validation
  Operations](research-log-mechanical-validator-spec.md#public-management-and-validation-operations);
- evidence sources, locators, transformations, presentations, and summary
  associations defined by [Evidence-Record Role And
  Scope](research-log-mechanical-validator-spec.md#evidence-record-role-and-scope)
  and [Evidence File And Presentation
  Association](research-log-mechanical-validator-spec.md#evidence-file-and-presentation-association);
- input, retention, command, output-support, provenance, lineage, and Hygiene
  behavior defined by [Input Registry And Artifact Graph
  Contract](research-log-mechanical-validator-spec.md#input-registry-and-artifact-graph-contract);
- evaluation, publication, generated-state, locking, dry-run, failure, and
  currentness behavior defined by [Mechanical Validation Evaluation And
  Outcomes](research-log-mechanical-validator-spec.md#mechanical-validation-evaluation-and-outcomes);
- cache, observation, and materialization behavior under the specification's
  [Resource And Safety Bounds](research-log-mechanical-validator-spec.md#resource-and-safety-bounds)
  and [Dependency Projection And
  Currentness](research-log-mechanical-validator-spec.md#dependency-projection-and-currentness);
- focused Record, Replace, Update Summary, Repair, Reorganize, and Validate
  behavior cases in
  [`skills/research-logging/tests/presented-evidence-cases.md`](../skills/research-logging/tests/presented-evidence-cases.md); and
- researcher-directed semantic Review routing, lens, composition, authority,
  and neighboring-workflow cases in
  [`skills/research-logging/tests/semantic-review-cases.md`](../skills/research-logging/tests/semantic-review-cases.md).

Run the complete research-logging tool gate after any validator change. Use the
focused controller, engine, evidence, command, locator, transformation,
provenance, material-graph, and publication tests during iteration.

Wall time is diagnostic rather than an objective gate. Require bounded
complexity, no avoidable repeated reads or hashes, correct cache reuse, and no
asymptotic regression.

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

Run it for changes to:

- `SKILL.md` names or descriptions
- selection boundaries between neighboring skills
- `tests/skill_selection_cases.csv`
- user-level skill layout or Codex discovery assumptions
- major `AGENTS.md` or runtime-context changes that could affect skill selection

Do not run the full skill-selection eval for routine documentation edits,
reference-file cleanup, or stale-path-only changes unless those edits affect
selection behavior. Treat failures as selection-regression signals that need
human review, not as proof that the skill can never work.

## Agent Behavior Evaluations

Use `scripts/agent_behavior_eval.py` for opt-in, multi-turn behavior
evaluations that need measured context use, compaction detection, or file-level
scope scoring. These evaluations are diagnostic engineering evidence. They are
not required CI and do not replace deterministic validation or review.

Run the non-model compatibility check first:

```bash
./.conda/bin/python scripts/agent_behavior_eval.py doctor
```

The check confirms the required Codex CLI commands and local persisted-session
directory. A real sequence additionally fails if JSON events, task identity,
token counts, or the model context window are unavailable in the expected
runtime format.

### Prepare An Evaluation

Create a skill snapshot in a new destination:

```bash
./.conda/bin/python scripts/agent_behavior_eval.py snapshot \
  --source skills/research-logging \
  --destination tmp/agent-eval/snapshots/baseline/research-logging
```

Define the turns in JSON. Prompt paths are relative to the sequence file, and
turn IDs must be unique lowercase identifiers:

```json
{
  "name": "terse continuation under context pressure",
  "turns": [
    {"id": "record", "prompt": "prompts/01-record.txt"},
    {"id": "continue", "prompt": "prompts/02-continue.txt"}
  ]
}
```

Give every trial a fresh workspace and artifact directory. Set the model and
reasoning effort explicitly:

```bash
./.conda/bin/python scripts/agent_behavior_eval.py sequence \
  --template tmp/agent-eval/fixture-template \
  --workspace tmp/agent-eval/trials/baseline-01/workspace \
  --snapshot-root tmp/agent-eval/snapshots/baseline/research-logging \
  --skill-name research-logging \
  --disable-skill "$PWD/skills/research-logging/SKILL.md" \
  --sequence-file tmp/agent-eval/sequence.json \
  --output-dir tmp/agent-eval/trials/baseline-01/artifacts \
  --model gpt-5.6-terra \
  --reasoning-effort medium
```

The runner copies the fixture, initializes it as a disposable Git repository,
and exposes the snapshot at both discovery and project-reference paths. Before
the first model turn, `codex debug prompt-input` must show exactly one skill
with the requested name at the snapshot path and none at any `--disable-skill`
path. The snapshot hash must remain unchanged throughout the trial.

The first turn starts a new Codex task; later turns resume only that task. The
runner intentionally does not use ephemeral mode because actual input use and
compaction are read from the persisted session trace. Raw prompts, JSON events,
stderr, the final session trace, and workspace state before and after each turn
are retained under the artifact directory. Treat those artifacts as potentially
sensitive task data.

Discovery verification inspects declared skill-list entries in the structured
prompt input. A skill path merely quoted in user or historical context does not
count as another discovered copy.

### Inspect And Score

Produce a generic, scorer-ready summary with:

```bash
./.conda/bin/python scripts/agent_behavior_eval.py inspect \
  --artifacts tmp/agent-eval/trials/baseline-01/artifacts \
  --output tmp/agent-eval/trials/baseline-01/inspection.json
```

The inspection reports actual peak input tokens, the observed model context
window, compaction signals, and added, changed, or deleted paths for every
turn. `first_post_compaction_turn` identifies the first completed turn after a
newly observed compaction boundary. The inspection does not decide whether
workspace changes are correct. Each evaluation must define its own allowed
changes, preserved files, inference boundary, and pass criteria outside the
agent being tested.

Freeze the fixture, prompts, pressure material, model settings, scoring rules,
and trial limit before collecting comparison evidence. Use repeated trials and
report outcome counts because model behavior is nondeterministic. Reject or
repair ambiguous prompts during calibration. Exclude compacted trials when
testing pre-compaction behavior; use `--compaction-policy continue` only when
compaction and subsequent behavior are themselves the test target.

These trials start model turns and can take several minutes each. Keep them out
of routine validation, and record the model-use and runtime cost when planning
replicated comparisons.

## Agent Surface Validation

- Changes to `AGENTS.md` files:
  - run `./.conda/bin/python scripts/validate_agent_surface.py`
  - run `$agent-surface-review`

- Changes to `SKILL.md`, skill references, or skill scripts:
  - run `./.conda/bin/python scripts/validate_agent_surface.py`
  - run `./.conda/bin/python scripts/validate_agent_surface.py --codex-discovery` when names, descriptions, metadata, or discovery layout change
  - run `./.conda/bin/python scripts/validate_agent_surface.py --skill-selection-eval` only when selection descriptions, skill names, discovery assumptions, or neighboring skill boundaries change
  - run `$agent-surface-review`
  - include focused prompt-writing, scope, or selection-boundary checks when the changed skill affects those behaviors

- Changes to documentation-surface review behavior:
  - run `./.conda/bin/python scripts/validate_agent_surface.py`
  - run `$documentation-surface-review`

- Changes to code-quality review behavior:
  - run `./.conda/bin/python scripts/validate_agent_surface.py`
  - run `$code-quality-review`

- Changes to upgrade planning behavior:
  - run `./.conda/bin/python scripts/validate_agent_surface.py`
  - run `$project-upgrade-planning`
