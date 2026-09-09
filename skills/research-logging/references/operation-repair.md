# Repair Operation Instructions

Correct the requested defects. Preserve presented evidence, scientific meaning,
tolerances, stable evidence IDs, and unrelated work. Do not invent origins or
execution confirmation. Follow `SKILL.md`'s operation and lock-conflict boundaries.

## Scope And Decisions

Work on a bounded correction in the requested log. It may span several batches;
batches do not dictate editing, validation, or reporting checkpoints. Do not
inventory the whole project or copy queryable results into a separate queue.

For missing lineage, check that the producer exists, declares the exact material,
is admitted by discovery, and precedes the consumer. Inspect the affected command
and script interface for facts the CLI cannot establish. Missing confirmation
alone does not explain missing lineage.

Fix what retained evidence establishes. Moving unchanged commands within a document
and small path, declaration, or output-exposure edits are ordinary Repair when
they preserve the computation and material identities. Use Reorganize for document
or entry boundary changes and Replace for removing superseded work; neither is
implicit in Repair.

Stop investigating once the correction or a blocking fact or choice is clear.
Report blockers, continue independent authorized work, and ask the researcher
when a decision is needed. Reuse a diagnosis only after checking that its cause
and safety conditions apply; an inspection group alone establishes neither.

## Inspect With The CLI

Run `log` through `<skill>/scripts/log`; `<log>` is the log directory. Use the
supplied result ID, or obtain the latest full result below. After validation,
inspect its returned result ID. These queries do not run validation.

| Need | Command |
|---|---|
| Evaluation scope and remaining work | `log results show --path <log> --latest --kind full` |
| Matching batches | `log results show --path <log> --id <result-id> --view batches [--entry <entry>] [--code <code>]` |
| Batch blocker and starting point | `log results batch --path <log> --id <result-id> --batch <batch-id>` |
| Finding evidence | `log results finding --path <log> --id <result-id> --finding <check-id>` |
| Commands and material relationships | `log results show --path <log> --id <result-id> --view commands --batch <batch-id>` |
| Recover lost batch-check stdout | `log results list --path <log> --kind batch --validation <validation-id> --batch <batch-id>` |

Request omitted detail through the printed command; follow cursors only as needed.
Use `--view chains` for provenance membership. For uncached published findings,
use `log findings list` or `show`, not another validation. Use `--help` for unfamiliar
syntax. Match recovered results to the invocation's scope and time; an older result
does not prove a failed or interrupted invocation completed.

Inspect validation state through CLI text views. Do not read, parse, or search
`validation.md`, `.cache/validation/results.json`,
`.cache/validation/batches.json`, or the inspection database. JSON output is
for programmatic consumers.

## Correct The Source

Use the owning action when it expresses the correction; otherwise edit the affected
source records or script. Preserve the original in version control or a backup.

| Defect | Owner |
|---|---|
| Input registration, target, or fingerprint | `log data` |
| Evidence record or association | `log evidence` |
| Disconnected-retention declaration | `log retention` |
| Recorded execution policy | `log pyrun` |
| Command material role | `references/file-entry-commands.md` |
| Provenance shape | Matching card in `references/provenance-patterns.md` |

`log data add-generated --pending-confirmation` admits a retained output with one
structurally valid, unambiguous same-log producer. It cannot bypass a missing
producer or replace ordinary pre-production declaration.
For an existing declaration whose files were restored, use
`log data refresh --pending-confirmation` to record their current fingerprint
under the same producer checks. This does not confirm execution.

Derive reconstructed fields from retained evidence; keep reconstructed execution
support `confirmed: false`. Never hand-edit generated validation. Only when malformed
state prevents the owning action, consult the relevant code or field in
`../../../docs/research-log-mechanical-validator-spec.md` and the needed file
contract. Remove transaction residue only as identified by its owning contract.

After a failed mutation, inspect its diagnostic. Retry only with a revised,
evidence-backed correction, never to recover stdout or bypass a precondition.

## Check The Correction

| Coverage | Command |
|---|---|
| Published batch's current membership | `log validate-batch --path <log> --validation <validation-id> --batch <batch-id>`; use originating IDs. |
| Full log and rebuilt report | `log validate --path <log>`; requires full-validation authorization. |

Use focused checks while editing, including an owning postcondition or decoder/test
for defects without a published batch. Group corrections needing full-log coverage
at one authorized full-validation checkpoint. Skip preliminary batch checks when
full validation is already needed.

Batch `complete_clear` and `complete_findings` cover only reconciled membership.
`coverage_incomplete` proves neither success nor failure: defer assessment to the
full-validation checkpoint. Do not try overlapping batches or repeat the batch/full
cycle for each edit needing that coverage. Resolve other evaluation failures before
judging the repair. Without full-validation authority, report the verification gap.

Reuse applicable completed results; query details instead of rerunning checks.
Claim clearance only when completed evaluation covers the corrected state.
Structural repair does not establish execution confirmation.
