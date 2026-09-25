# Repair Operation Instructions

Correct the requested defects. Preserve presented evidence, scientific meaning,
tolerances, stable evidence IDs, and unrelated work. Do not invent origins or
clear an execution's reproduction requirement. Follow `SKILL.md`'s operation
and lock-conflict boundaries.

An active reproduction run reserves only overlapping reproduction targets. It
does not hold an ordinary log or entry mutation lock for its lifetime, so an
authorized repair may take the normal short operation lock and update its own
state. Do not infer that a repair changes an accepted plan: a run retains no
whole-log source snapshot and cannot adopt edited commands, declarations, or
evidence on resume. Start a new reproduction run after a repair when execution
against the changed source is required.

## Scope And Decisions

Work on a bounded correction in the requested log. It may span several batches;
batches do not dictate editing, validation, or reporting checkpoints. Do not
inventory the whole project or copy queryable results into a separate queue.

For missing lineage, check that the producer exists, declares the exact material,
is admitted by discovery, and precedes the consumer. Inspect the affected command
and script interface for facts the CLI cannot establish. A reproduction
requirement alone does not explain missing lineage.

Fix what retained evidence establishes. Moving unchanged commands within a document
and small path, declaration, or output-exposure edits are ordinary Repair when
they preserve the computation and material identities. Use Reorganize for document
or entry boundary changes and Replace for removing superseded work; neither is
implicit in Repair.

Stop investigating once the correction or a blocking fact or choice is clear.
Report blockers, continue independent authorized work, and ask the researcher
when a decision is needed. Reuse a diagnosis only after checking that its cause
and safety conditions apply; batch membership alone establishes neither.

## Inspect With The CLI

Run `log` through `<skill>/scripts/log`; `<log>` is the log directory. The
version-19 validation authority uses stable finding and batch identities, with
batch detail supplying the mechanically relevant repair packet. It has no
generic result ID, human issue group, command chain, or unresolved-group view.

Use `log validate show --path LOG` for orientation, `log validate list batches
--path LOG` for repair units, and `log validate detail batch --path LOG --id
BATCH_ID` for the mechanically assembled repair packet. Use finding list/detail
when diagnosing one atomic finding. Do not use removed generic result
commands, parse the SQLite store, or reconstruct a repair packet from
`validation.md`. Do not rerun validation merely to recover inspection output.

Most batches share an exact repair key or causal relationship. A batch whose
rationale starts with `orphan-singletons:` instead collects otherwise-singleton
Orphans findings with the same entry and exact code for efficient orientation.
Inspect its member subjects before correction; that fallback does not establish
a shared cause or make one bulk correction safe for every member.

Inspect validation state through CLI text views. Do not read, parse, or search
`validation.md`, `.cache/results.sqlite`, or its SQLite companions. JSON output is
for programmatic consumers.

## Correct The Source

Use the owning CLI action for valid registries; edit Markdown or scripts first
when that action consumes them. For a well-formed `data.json` blocked only by
symlinked locations, use `log data repair-locations` for every affected name
with direct paths to the same material. Direct JSON editing is reserved for
explicitly authorized malformed state the CLI cannot repair. Preserve the
original in version control or a backup.

| Defect | Owner |
|---|---|
| Missing command/evidence declaration | The consumer's `log command sync` or `log evidence sync` add clause |
| Shared material target, boundary, kind, identity, reproduction policy, material-name rename or deletion | `log data` |
| Retained execution-observation mismatch | Report the mismatch and request explicit Reproduce authority for confirmation; promotion or a fresh `./pyrun` requires a separately authorized researcher decision |
| Evidence record or association | `log evidence` |
| Disconnected-retention declaration | `log retention` |
| Recorded execution policy, recipe, CID rename, or command deletion | `log command sync`; edit Markdown first, then synchronize the complete selected CID set in one dry-run/apply pair |
| Provenance shape | Matching card in `references/provenance-patterns.md` |

Use the producer's `log command sync --add-generated` or
`--add-generated-directory` for a retained or pre-production output.
The same call validates its unique producer and declares its target without
accepting historical output bytes or changing execution observations.

For command edits, read `references/file-entry-commands.md`, including “Write A
Recorded Command” and “Synchronize A Recorded Command”; do not hand-edit
`pyrun.json`.

Derive reconstructed fields from retained evidence; keep reconstructed execution
support at `requires_reproduction: true`. Never hand-edit generated validation. Only when malformed
state prevents the owning action, consult the relevant code or field in
`../../../docs/research-log-mechanical-validator-spec.md` and the needed file
contract. Remove transaction residue only as identified by its owning contract.

After a failed mutation, inspect its diagnostic with the exact recovery command
printed by the failure: `log command show --path LOG --id DIAGNOSTIC_ID`. Retry
only with a revised, evidence-backed correction, never to recover stdout or
bypass a precondition.

## Check The Correction

The isolated command-verification operation is synchronous. It never creates a
reproduction run, changes metadata, clears a requirement, publishes a result,
or promotes outputs. It compares current declared-input fingerprints with their
recorded observations and compares isolated regenerated outputs with retained
output baselines. Source edits during the call are unavailable, not adopted.
Run it with `log command verify --path LOG --entry ENTRY --cid CID
--execution-id ID`.

| Coverage | Command |
|---|---|
| Current repair findings | Validate each affected stable entry with `log validate run --path <log> --entry eNNN`; inspect repair batches with `log validate list batches --path <log> --entry eNNN` and `log validate detail batch --path <log> --entry eNNN --id BATCH_ID`, then run one final full validation. |
| Full log and rebuilt report | `log validate run --path <log>`; requires full-validation authorization. |

Use focused checks while editing, including an owning postcondition or decoder/test.
Group corrections needing full-log coverage at one authorized full-validation
checkpoint. An entry validation outcome is bounded current inspection, not certification;
defer complete-log assessment to the
full-validation checkpoint. Do not try overlapping batches or repeat the batch/full
cycle for each edit needing that coverage. Resolve other evaluation failures before
judging the repair. Without full-validation authority, report the verification gap.

Reuse applicable completed evaluations; query saved details instead of rerunning
validation.
Claim clearance only when completed evaluation covers the corrected state.
Structural repair does not clear an execution's reproduction requirement.
