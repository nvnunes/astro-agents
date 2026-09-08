# Agent Behavior Evaluations

Use this optional reference when an evaluation is explicitly chosen. Ordinary
change validation starts at [Testing](../testing.md). Commands run from the
project root using the [repository environment](agent-surface.md#environment-and-deterministic-checks).

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
