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
- activation eval fixture shape in `tests/activation_cases.csv`
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

When changing research-log section classification, evidence presentation, or
validation behavior, also run:

```bash
./.conda/bin/python -m unittest discover \
  -s skills/research-logging/tests -p 'test_research_log_validation*.py'
```

For any research-logging tool change, run the complete tool gate rather than
linting only the main validator:

```bash
./.conda/bin/python -m py_compile skills/research-logging/scripts/pyrun \
  skills/research-logging/scripts/research_log_data.py \
  skills/research-logging/scripts/research_log_validation.py \
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

Treat maintained summaries, entries, `data.json`, `retention.json`, evidence
records, commands, scripts, retained evidence, scientific artifacts, and
authored prose as research-owned. Mechanical validation may write only the generated artifacts
defined in
`skills/research-logging/references/file-validation-records.md`. Research
operations must leave every existing generated validation file byte-identical.

The research-log test gate must verify:

- the public CLI accepts only `validate`, `--summary`, `--date`,
  `--recompute`, and `--dry-run`;
- a published validation prints a bounded result with generated-report paths
  instead of duplicating the complete mechanical record on standard output,
  while an unpublished dry run retains its complete record;
- complete-clear, complete-findings, and unsupported-metadata results exit zero,
  while incomplete evaluation and tool failure exit nonzero;
- recognized unsupported generated state produces one precise
  `validation.unsupported_metadata` result, reports every detected path, and
  writes nothing;
- the active standard route imports no semantic review, decision,
  continuation, reproduction, or unsupported evidence runtime;
- `validation/mechanical.json` uses schema
  `research-log-mechanical/1`, while the disposable per-log cache uses the
  independent `research-log-mechanical-cache/6` schema;
- the project-level fingerprint cache uses SQLite schema version 1 at
  `<project>/.cache/research-log-fingerprints.sqlite3`, remains independent of
  mechanical rules and report schemas, and is excluded from source control;
- the nearest enclosing non-symlink Git worktree marker owns the project cache,
  including when the maintained log is outside `docs/` or the project itself is
  named `docs`;
- cache absence, corruption, or an unsupported cache schema causes bounded
  recomputation, and evaluated checks count as unchanged only when the complete
  cache contract, rules version, dependency projection, and check content
  match;
- a rules-version change makes cached checks ineligible for unchanged
  comparison while preserving artifact identities whose cache entry and
  current file identity still match exactly;
- cached artifact identities avoid rehashing only when the project-relative
  regular file has the same byte size, modification time, and change time;
- newly hashed scripts and locator sources publish digest and filesystem
  metadata from one unchanged before-and-after observation, and a concurrent
  change is unavailable rather than cacheable;
- project-cache file observations avoid rehashing local files, including
  external paths, only when canonical path, kind, size, modification time, and
  change time still match;
- project-cache directory observations retain deterministic membership and
  member-file identities, reuse unchanged members after a partial change, and
  hash only new or changed files before reconstructing the aggregate
  fingerprint;
- identity-file managed-directory observations hash only their 1–64 exact
  declared files, never traverse undeclared descendants, reuse unchanged files
  across logs, and create one logical aggregate command relationship rather
  than false descendant membership;
- identity-pattern managed-directory observations allow 1–64 final-component
  selectors and 1–64 unique matches, scan each wildcard parent once, stop after
  100,000 immediate candidates, and never traverse undeclared descendants;
- repeated declarations, overlapping directories, and different logs in one
  project share one content observation by canonical path;
- schema-5 per-log input observations and compatible artifact identities seed
  the project cache when current filesystem metadata still matches, while one
  hydration scan captures directory-member hashes omitted by the old cache;
- completed file observations survive interruption, concurrent validators do
  not hash the same uncached file twice, and a corrupt generated project cache
  is rebuilt without changing validation semantics;
- dry-run treats unusable project cache state as absent, preserves unsupported
  future schemas, and recompute dry-run never opens the project cache;
- rebuilt mechanical caches contain only artifact observations used by the
  current evaluation, so removed artifacts do not persist as stale per-log
  cache state;
- `--recompute` bypasses every existing mechanical and fingerprint-cache entry,
  publishes a newly rebuilt mechanical cache after a completed run, and writes
  nothing when combined with `--dry-run`;
- `validation.md` contains separate Mechanical Validation and Reproduction
  sections, shows reproduction as `not_yet_run`, and has no combined
  conclusion;
- the mechanical report shows completion and date, check counts for
  conformance and evidence, unique starting-artifact counts for provenance,
  unique artifact counts and maximal directory groups for orphans, unused
  input declarations separately, and every other non-passing check grouped by
  entry without rendering individual passing checks;
- canonical discovery finds maintained summaries from their stable navigation
  contract without filename-based exclusions;
- dry-run writes nothing, incomplete evaluation publishes no per-log bundle,
  completed fingerprint observations may persist from a writable incomplete
  run, and an ordinary publication failure restores the prior per-log bundle;
- validation leaves all research-owned bytes unchanged and preserves the
  maintained summary's exact stable report link;
- evidence comparison, provenance, summary forwarding, and orphan detection
  remain independent code-only scopes with precise failure payloads;
- external evidence is observed as a dependency of the current log without
  reading another log's validation state;
- unchanged dependency projections produce matching passing checks, while
  changed dependencies alter only affected checks;
- source observations, locator evaluations, script hashes, and command
  discovery stay bounded and shared within one invocation, including both
  per-member and aggregate binary-materialization limits;
- local source and input paths reject lexical symlink aliases outside the
  exact entry `data` and `images` roots or a platform alias shared by the entry
  and target;
- material-graph depth overflow fails explicitly rather than silently
  truncating producer lineage; and
- active implementation, test, fixture, command, and source-of-truth filenames
  use stable version-neutral names. Version labels remain only in data formats,
  schema identifiers, and unsupported-metadata observations.

Run the complete research-logging tool gate after any validator change. Use the
focused controller, engine, evidence, command, locator, transformation,
provenance, material-graph, and publication tests during iteration.

Wall time is diagnostic rather than an objective gate. Require bounded
complexity, no avoidable repeated reads or hashes, correct cache reuse, and no
asymptotic regression.

Use
`skills/research-logging/tests/presented-evidence-cases.md` as the focused
manual behavior cases for Record, Replace, Update Summary, Review, and Validate
changes.
## Codex Runtime Discovery

Run the Codex runtime discovery smoke test when changing skill names, skill descriptions, `agents/openai.yaml`, the user-level skill layout, or downstream usage guidance:

```bash
./.conda/bin/python scripts/validate_agent_surface.py --codex-discovery
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
./.conda/bin/python scripts/validate_agent_surface.py --activation-eval
```

This runs each fixture row through `codex exec` in ephemeral read-only mode and asks Codex for a compact JSON activation decision. Because it starts a model turn per fixture row, the full activation eval is comparatively expensive and should not be part of routine validation.

Run it for changes to:

- `SKILL.md` names or descriptions
- activation boundaries between neighboring skills
- `tests/activation_cases.csv`
- user-level skill layout or Codex discovery assumptions
- major `AGENTS.md` or runtime-context changes that could affect skill selection

Do not run the full activation eval for routine documentation edits, reference-file cleanup, or stale-path-only changes unless those edits affect activation behavior. Treat failures as activation-regression signals that need human review, not as proof that the skill can never work.

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
  - run `./.conda/bin/python scripts/validate_agent_surface.py --activation-eval` only when activation descriptions, skill names, discovery assumptions, or neighboring skill boundaries change
  - run `$agent-surface-review`
  - include focused prompt-writing, scope, or activation-boundary checks when the changed skill affects those behaviors

- Changes to documentation-surface review behavior:
  - run `./.conda/bin/python scripts/validate_agent_surface.py`
  - run `$documentation-surface-review`

- Changes to code-quality review behavior:
  - run `./.conda/bin/python scripts/validate_agent_surface.py`
  - run `$code-quality-review`

- Changes to upgrade planning behavior:
  - run `./.conda/bin/python scripts/validate_agent_surface.py`
  - run `$project-upgrade-planning`
