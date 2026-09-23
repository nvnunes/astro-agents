# Research-Log Reproduction Specification

This is the normative runtime contract for mechanical reproduction, accepted
jobs, saved results, comparison, publication and promotion. Usage guidance
belongs to [Research Logging](research-logging.md) and the research-logging skill.
Verification commands and coverage belong to [Research-Logging Validation](testing/research-logging.md).
Temporary implementation plans do not define runtime contracts.

The key words **must**, **must not**, **should**, and **may** are normative.

## Replacement Reproduction Model

Commands and artifacts are separate counted units. Owned problems explain
work and observed outcomes; dependency and producer links carry effects without
copying causes to every output. Selection, admission, retry, safety, comparison
and publication behavior remain distinct from presentation.

### Canonical Records And Versions

The current formats are plan/14, result/14, job-store 6 and shared-store 22.
Execution state is `research-log-pyrun/v7`; worker, scheduler and comparison
families retain their existing versions. No obsolete reproduction result or job
is decoded, migrated or resumed. Unsupported saved results require a new
`log reproduce run --path LOG --recheck`.

An execution identity is the closed compound object `entry`, `cid`,
`execution_id`. An artifact identity is `entry`, `artifact`, preserving existing
entry-relative, `<project>/` and canonical absolute identities. Same execution
digests in different entries or CIDs remain different units. Artifact totals
count existing reachable cases, not automatically every declared output;
complete declared outputs remain available for execution safety and promotion.

| Record | Closed Fields |
| --- | --- |
| Command work | `identity`, `execution`, `entry_root`, `project_root`, `data_declaration`, `selection`, `source_digest`, `dependencies`, `problem_ids`, `accepted_source` |
| Artifact work | `identity`, `producer`, `output`, `retained_path`, `baseline`, `definition_identity`, `evidence_records`, `boundary`, `problem_ids` |
| Problem | `subject`, `code`, `stage`, `observed`, `explanation`, `locations` |
| Command result | `identity`, `outcome`, `started_at`, `finished_at`, `argv`, `cwd`, `stdout_path`, `stderr_path`, `outputs`, `problem_ids`, `blocked_by` |
| Artifact result | `identity`, `outcome`, `recorded_at`, `origin_run_id`, `regenerated_path`, `profile`, `expected`, `regenerated`, `not_compared_reason`, `evidence`, `problem_ids`, `definition_identity` |
| Accepted plan | `schema`, `summary`, `target`, `settings`, `admission`, `commands`, `artifacts`, `problems`, `materials`, `evidence_only`, `scheduling`, `reusable_artifact_results` |
| Saved run | `schema`, `summary`, `run_id`, `target`, `settings`, `accepted_at`, `finished_at`, `status`, `commands`, `artifacts`, `command_results`, `artifact_results`, `problems` |

`execution` retains the existing typed `PyrunExecution` recipe and historical
observations. `accepted_source` is either null or the current raw top-level
script fingerprint plus a nullable effective-code fingerprint accepted for
this plan; every runnable command has it. It is execution stability and
reconciliation authority, not a
second recipe or output baseline. The
automatic/exclusive policy and the reproduction-need flag are not duplicated
as separately authoritative flags. Resolved `data_declaration` uses the existing
accepted data grammar. Evidence definitions and comparison observations retain
their existing profile-specific grammars; their immutable enclosing records
do not invent a second evidence model. The preparation/comparison boundary
continues to validate these definitions using the existing evidence owner.

Each artifact freezes only the records selected by its existing evidence
comparison definition, including every applicable selector and tolerance.
Unrelated artifact records are not copied into its packet. Frozen reconstruction
uses that same single-resource definition; it does not require unrelated
definitions to be reconstructed from this artifact's packet.

An artifact's `producer` and declared `output` are either both known or both
null. Known bindings point to an output declared by the accepted producer recipe;
directory members retain that declared-directory binding rather than inventing
a separate producer output. Saved inspection does not rediscover this relation.

Targets are exactly `{"kind":"log","entry":null}` or
`{"kind":"entry","entry":"e001"}`. Settings are exactly `include_all`,
`recheck`, `jobs`, `execution_timeout_seconds`; booleans are not integer counts.
Jobs are positive integers; the existing timeout range remains 1–604,800 seconds.
Saved-run `status` is `complete`: stopped or operationally failed unpublished
jobs remain lifecycle state and do not replace the default saved run.

A problem subject is a discriminated command identity (`kind: command`),
artifact identity (`kind: artifact`) or canonical source identity
(`kind: source`, `path`). Its stages are `prepare`, `launch`, `capture`,
`execute`, `materialize`, `compare`. Its derived local ID is `problem-` followed
by SHA-256 of canonical subject/code/stage/observed facts. Wording, locations
and consumer lists do not define identity. Complete code-specific finite JSON
facts are deeply immutable; non-string object keys, unknown envelope fields,
partial identities and noncanonical paths fail closed. Problem references
retain deterministic mechanical precedence. Distinct observed causes remain
distinct even on one subject; there is no fuzzy or cross-run causal deduplication.

Preparation uses `research-log-reproduction-command-source/3` for the replacement
source digest. It hashes the recorded recipe and retained input/output
observations, currentness observations, accepted current effective code,
materials, output comparison identities,
dependencies and all currently
observed owned preparation problem identities. It does not hash synthesized
cases, the mutable reproduction requirement, retained raw/effective source
observations or a primary
display reason. A change in a secondary observed cause therefore invalidates
the same guard even when the compact primary reason stays unchanged. Wording
and location-only changes do not alter problem identity.

Retained previous-attempt/block diagnoses belong to immutable accepted history,
not current source identity. They remain complete in the accepted work and its
serialized plan without mutating preparation state or causing an unchanged
previous failure/block to retry merely because its diagnosis is retained.
The same closure guard suppresses an unchanged prior successful production whose
complete comparison set contained `not-matched` or `not-compared`; its exact
saved comparisons are reused. Recheck, a source-closure change, a baseline or
comparison-definition change, or cleared saved results selects fresh work.

Preparation reads the requested native latest origins once per origin, retaining
only requested command/artifact facts and their owned prior root/prerequisite
and comparison diagnoses, not a cache of whole historical runs. SQL preflight
bounds the combined native payload bytes of all distinct origins at 64 MiB before
any full-run authentication/deserialization. Retained requested facts also share
the 64 MiB budget. Reads authenticate complete immutable origins; retention,
not database transfer, is sparse. Previous blocks follow existing dependency
edges: a currently unneeded prerequisite can own the retained cause, while its
consumer carries no copied problem and unrelated history stays excluded.

Saved runs remain immutable history. Minimal latest-command origins advance
only for selected `run`/`blocked` work; latest-artifact origins advance only
for recorded artifact results. A selected producer invalidates its previously
indexed output observations. Nonselected previous failure/block keeps its
original origin, rather than making the new no-attempt run appear to own it.

Normal publication combines only accepted work and durable native observations.
Missing terminal facts for runnable work prevent publication; they never become
a fabricated match. Nonattempted artifacts derive their reason from the recorded
producer. When that producer lies outside the accepted target, retained boundary
context supplies `command-not-run` for `cross_entry`/`outside_queue` or
`skipped-by-policy` for `non_automatic`, without inventing a command in the target.
An ownerless diagnosed preparation/comparison inability retains its artifact
cause as `comparison-failed`. A valid comparison frozen for reuse keeps its exact
original run/time and observed facts.

The closed mechanical problem codes are `baseline_unavailable`,
`baseline_changed`, `boundary_changed`, `boundary_unavailable`, `capture_failed`,
`comparator_error`, `content_changed`, `cross_log_generated_input`,
`dependency_cycle`, `direct_input_changed`, `direct_input_unavailable`,
`execution_exception`, `execution_failed`, `execution_timeout`,
`evidence_comparison_failed`, `evidence_context_changed`, `generation_failed`,
`graph_limit`, `missing_input`,
`missing_producer`, `multiple_producers`, `output_materialization_failed`,
`output_missing`, `effective_code_changed`, `effective_code_unavailable`,
`reproduction.input.unavailable`, `resource_limit`, `safety_failure`,
`unsupported_format`, `validation_blocked`.
Policy, dependency effects, stop/cleanup and operational `reproduction.run.invalid`
are not additional research-problem codes.

Accepted admission retains its existing closed schema and complete per-command
finding-owned decisions. Materials retain exact role/identity/kind/fingerprint
and input selection fields; evidence-only observations retain their existing
resource/selection/fingerprint and consuming definition identities. Scheduling
rows contain exactly `identity`, `order`, `read_paths`, `write_paths`, `run_path`,
`writable_paths`. Order preserves the existing complete one-based sequence and
selected dependencies precede consumers. Policy flags and complete output sets
are obtained from command work, not persisted again in scheduling rows.
Only completed, inventory-qualified prior comparisons are eligible for reuse;
their referenced diagnoses are retained with the accepted plan.

Artifact results retain the exact accepted `definition_identity` when evidence
defines the comparison, independently of the profile actually used. The original
comparator first tries its byte/typed rule and invokes the evidence profile only
for changed content; an equal text/array comparison still retains the accepted
evidence definition. The identity is null when no evidence definition applies.
This is the aggregate single-resource comparison identity. Individual retained
evidence records keep their existing per-selector definition identities, which
are a different identity space and are not required to equal the aggregate.
This preserves the existing evidence-definition fact rather than inferring it
from today's registry. Reuse is eligible only for nonselected work, the same
accepted baseline, and exactly equal work/result `definition_identity` for every
comparison profile, including matching null when no evidence definition applies.

Native run-local acceptance stores normalized work/relationships and one
canonical plan digest. Reconstruction must agree with that digest; valid but
altered relationships or header facts are not silently reaccepted. Preparation
and terminal observations reference one shared run-local problem store.
Acceptance membership identifies only the problems frozen in the plan, so a
runtime diagnosis cannot mutate the accepted plan. Terminal results retain their
own canonical digests and exact command/artifact relationships. Transaction and
operational checkpoint ownership remain with the job store.

The native Job6 executor, `execute_work_recipe`, runs only same-identity
frozen accepted work under the existing process, confinement, source/input and
materialization guards. Native command result/problem and exited worker state
commit atomically before scratch cleanup and caller-owned scheduler release.
A terminal-write failure retains active scratch/grant ownership for recovery;
a release failure retains the committed result and recovery state. Stopped work
creates no terminal research result. Comparison submission is internal to
`compare_work_outputs`, which binds accepted work and its generated workspace
paths to the existing comparator rather than accepting caller-supplied results.
Completed-run assembly reads only accepted/durable facts and requires cleaned,
quiescent workers/grants, with SQL aggregate observation-byte preflight before
decoding. Native scheduler admission/reconciliation uses the existing project
fairness, conflict and ticket rules, with only accepted claims, supervisor,
attached-grant and live-worker proof. It opens only native jobs, including dead
permit owners; unsupported owners require explicit resolution, not fallback.
Native stop/failure intent and fixed-plan resume remain operational controls,
not research outcomes. The native execution-graph stage gets runnable identities
and fixed worker/timeout settings from accepted work. Failed prerequisites retain
only actual block links, without an invented dependent attempt. Completed work
must pass the existing private-output currentness/materialization guards before
reuse; a local stop becomes durable before waits are canceled. Native state
operations use short SQLite mutations and coherent read-only snapshots. Stop
records durable intent without taking the supervisor's lifecycle ownership;
the supervisor acknowledges it and drains workers. Status reads neither process
state nor current research sources and never initiates recovery. Sustained
storage contention is an explicit control-plane failure, not a routine
worker-observation retry.

A successful producer's accepted artifacts are compared before its consumers
become ready. Each consumer is bound to the exact producer artifacts named by
its accepted inputs. `matched` satisfies that edge; `not-matched` or
`not-compared` blocks only consumers of that artifact, with the existing
artifact-owned problem referenced by the blocked command. Other outputs from
the same producer and independent branches continue. These are normal saved
research outcomes, not run-level operational failures.
Native dead-owner recovery returns without mutations or process inspection for
a live owner. It uses the existing run-ID-marked worker scan and termination,
retains every survivor durably, and leaves permits, scratch and ownership open
while cleanup is incomplete. Only an exhaustive no-survivor scan permits exact
grant reconciliation, confined scratch cleanup and terminal ownership closure.
Ordinary interruption becomes stopped, preserving any real operational failure;
interrupted publication becomes the publication-only-resumable failure with its
frozen journal intact. Recovery reads no research sources, replans nothing and
executes nothing. Ordinary public launch/status/stop/resume and explicit
recovery consume this native accepted-job authority without an old-format fallback.
One stop request waits for the supervisor's durable stopped result, another
terminal result, or an exact recovery/control failure. Ordinary operationally
failed runs remain terminal; only stopped runs and failed frozen publication
may resume. A stopped attempt's exited worker observations do not have to
reappear in its next attempt. An unrelated log's run is ignored before deeper
state or process inspection during planning.

The `supervise_work_job` lifecycle requires the exact live native owner
and composes the fixed accepted execution graph, trusted comparison and native
publication. Fresh/stopped routes use the accepted workspace and existing
physical preflight; stop closes only quiescent stopped ownership and uncertain
operational cleanup remains recoverable. Publication-only retry consumes frozen
completed facts without opening a workspace, preflighting, executing or replanning.
Ordinary `log reproduce run` freezes fresh native preparation and registers the
actual detached supervisor before releasing its inherited start gate. The
supervisor executes, compares the complete declared output set, acknowledges
eligible reproduction-requirement clearing, and publishes immutable saved facts.
Source reconciliation retains the exact accepted/current recipe and observation
guard and job-then-entry lock order. Its acknowledgment follows the atomic
`pyrun.json` write, so retry can acknowledge an already-applied change without
executing. Complete production clears `requires_reproduction` even when an
artifact is unequal or cannot be compared. When every declared artifact is
canonically `matched`—including the empty set for a zero-output command—the same
atomic write also adopts the accepted raw-script fingerprint and nullable
effective-code observation. A null observation remains noncurrent for later
selection.
Otherwise the retained source observations and outputs stay unchanged.
Partial/failed/blocked/stopped work cannot reconcile early. Native status exposes lifecycle/checkpoint progress and
available retained diagnostics even before saved publication.

Native publication commits an immutable saved run and advances the reproduction
generation atomically. `publish_saved_run` replaces unsupported reproduction
cache tables only, without translating old rows or changing validation and
command-diagnostic domains. An exact run-ID retry or uncertain-acknowledgement
lookup through `lookup_saved_run_generation` recognizes the committed run at
the current generation; it does not reapply latest indexes or lower that
generation after another run publishes. Conflicting facts for an existing run
ID fail closed. Compact report materialization is subsequent and recoverable
without execution or replanning. Native job publication additionally freezes its
completion time before any shared write, acknowledging result and report
generations in a minimal recovery journal, without copying the saved payload.
`publish_work_job` retries the exact accepted/durable facts after lost result
acknowledgment, report failure, terminal acknowledgment failure, or loss of the
disposable shared result domain. Only actual report acknowledgment closes the
job and supervisor lease; already completed publication is non-mutating.
A publication failure can resume only with its frozen journal, complete durable
facts, quiescent attempts and an absent or exited prior supervisor lease. Resume
clears operational failure and returns to publishing; it cannot admit execution
permits or replan. Ordinary job acceptance, supervision and publication now use
this lifecycle. Explicit promotion resolves the native complete staged output
observations and preserves the existing baseline/confinement/reservation and
copy/rollback guards. It does not rewrite saved historical outcomes or their
latest-observation indexes; report recovery renders the committed saved summary.

Explicit whole-log recheck with no command or artifact work can replace obsolete
reproduction history with an empty-confirmation receipt. The receipt records only
canonical summary, confirmation time and domain generation; it invents no run or
execution. Saved summary/report show confirmed zero totals, lists are empty and
explicit run IDs remain missing. Absent or supported history remains unchanged.
A receipt retry can recover an interrupted report write, and normal publication
removes the receipt. Version 22 is the single current replacement format,
including this receipt table. Earlier reproduction formats and incomplete staged schemas are
obsolete, unsupported, and replaced without decoding or migration.

Detail pages known recipe/result/diagnosis/output collections using `--section`
and `--cursor`. Each page returns at most 50 items, exact matched/returned/remaining
counts and a shell-safe continuation command. Cursors bind immutable run identity,
compound item identity, domain generation, section and output format. The scalar
outcome and primary explanation remain visible; collection paging changes no
saved fact.

Comparison diagnosis is captured during the existing comparison, not by an
extra reporting pass. It includes the first available differing line, JSON
path, table row, byte offset or directory member; expected/regenerated summaries
are bounded and explicitly mark truncated values. Actual caught error type and
message survive category classification. Evidence comparisons retain their
existing complete per-evidence records and identify differing evidence IDs.
The existing `kind` profile remains a valid unequal comparison. These additions
do not change normalization, tolerances, equality, resource limits or baseline
rejection. The caller still verifies the accepted evidence-only context before
comparing outputs; changed context records `evidence_context_changed` rather
than accepting a comparison against a different definition.

Saved records retain one problem at its actual owner. Dependency and producer
links carry effects; copied per-output failures, summary buckets, independently
persisted accounting reasons and totals are absent. Unknown/duplicate identities
and dangling references fail closed. Saved command results cover run-selected
commands; locally blocked work may be represented by its preparation cause or
a blocked result. Artifact results cover every counted artifact. A carried
comparison retains its original run/time instead of pretending it was newly
performed. A missing prior comparison never becomes an invented match.

Terminal command results derive directly from the same-identity actual
invocation, completion time and valid unique declared output observations,
not by reconciling a second checkpoint outcome. Success requires zero exit,
no execution/capture/materialization failure and the complete declared output
set; missing outputs produce a failure naming the exact missing members.
Failed execution retains valid partial outputs, original return code and caught
code/type/message/stage. Capture name, required flag, error type and message
remain in the command-owned diagnosis even when another error is primary.
Materialization records the actual caught error in that same invocation.
Stopped attempts produce no terminal research result; prerequisite-blocked
commands retain only prerequisite links, never invented invocation/output facts.

An artifact-owned preparation diagnosis may be referenced by its accepted
producer when the baseline guard blocks that command; the single diagnosis
remains artifact-owned. This uses the existing producer binding, not another
causal graph. Unrelated owners and unreferenced diagnoses are invalid. Shared
source diagnoses must refer to facts actually retained by their accepted consumers.

### Classification And Count Equations

Selection leaves are `run`, `blocked`, `not_needed`, `previous_failure`,
`previous_block`, `skipped_by_policy`. Attempt outcomes are `succeeded`,
`failed`, `blocked`; blocked means no attempt. Launch/capture/materialization
failures inside an attempt are failed command results, not prerequisite blocks.
A failed producer or an unsatisfied producer-artifact comparison blocks
dependent attempts through the existing dependency links. Independent work may
proceed. Operational persistence and cleanup failures stay at run lifecycle
ownership.

The single command classifier derives `not-run`, `skipped-by-policy`,
`succeeded`, `failed`, `blocked`. Not-run reasons are `not-needed`,
`previous-failure`, `previous-block`; failed/blocked reasons are exact retained
mechanical cause codes selected by deterministic existing precedence, never
parsed from prose. Detail retains all contributing causes, not just that primary
reason. `--status selected` means succeeded, failed or blocked.

Artifact statuses are `matched`, `not-matched`, `not-compared`. Non-comparison
reasons are exactly `command-failed`, `command-blocked`, `command-not-run`,
`skipped-by-policy`, `comparison-failed`. Unequal outputs do not turn a succeeded
command into a failed command. Completed comparisons retain profile and
expected/regenerated observations; unequal/error comparisons retain their
artifact-owned pinpointing diagnosis. Missing diagnostic files qualify
availability, never the saved classification.

The hierarchy obeys these equations over the complete recorded target:

```text
Commands total = Not run + Skipped by policy + Selected
Not run = Not needed + Previous failure + Previous block
Selected = Succeeded + Failed + Blocked
Failed = sum(Failed reason counts)
Blocked = sum(Blocked reason counts)
Artifacts total = Matched + Not matched + Not compared
Not compared = sum(Not compared reason counts)
```

Reason counts describe affected units, not the number of root problems. Counts,
list status/reason filters and detail use the same classifier. Filter matching
is exact primary-reason matching; filters do not search diagnostic prose or
secondary causes. Unknown statuses/reasons and incompatible selector/filter
combinations are errors. Plan counts instead describe Ready to run, Blocked,
Not needed, Previous failure, Previous block and Skipped by policy; they predict
neither successful execution nor artifact matches.

### Ownership Mapping And Removal Conditions

| Existing Family Or Representation | Replacement Owner And Preserved Meaning |
| --- | --- |
| `_Failure` script unavailable/changed | One execution-owned preparation problem with exact script path and expected/observed/error facts; shared source ownership when the exact script is shared. Affected work references it. |
| Participating code unavailable/changed | One source-owned preparation problem per exact canonical code subject and observation, referenced by its consumers; same-basename unrelated files stay distinct. |
| Missing/direct input unavailable or changed; retained boundary unavailable or changed | The actual input/source subject and its preparation facts; consumer prerequisites or ownerless artifact boundary cases retain their existing scope and eligibility. |
| Missing/multiple producer; cross-log generated input; dependency cycle | The actual unresolved artifact/source or cycle-owned preparation cause, with existing producer/dependency links; no invented command for an ownerless artifact. Preserve local blocks and independent progress. |
| Baseline unavailable/changed | Artifact-owned preparation/comparison diagnosis at the existing detection boundary; retain baseline guard behavior rather than allowing a different baseline. |
| Validation exclusion | Retain each applicable blocking validation finding and its bounded diagnosis separately; affected command work references that finding-derived cause. Orphans stay nonblocking. Do not aggregate findings into a combined Reproduce problem or repair packet, or copy a finding into one failure per output. |
| `non_automatic`, policy/outside-queue case | Selection/boundary handling, not an attempted failure. Preserve not-needed-before-policy precedence and include-all independence. |
| `outside_entry` verified case | Existing accepted scope/boundary, not a failed command. Do not count a command outside the target just to own this artifact. |
| Currentness / `requires_reproduction` / `source_digest` | Preparation only; exact need, unchanged failure/block/completed-difference suppression and complete source-closure invalidation. A match does not decide initial command eligibility; the complete comparison set decides only post-run source reconciliation. |
| Old command snapshot and synthesized `details` | Canonical command work; original recipe/data/roots and all problem references, without duplicated eligibility flags or case-derived reason lists. |
| Checkpoint child exit, timeout, execution exception or generation failure | Command result and one command-owned attempted-execution problem with original stage/code/message/type, timing, invocation and available output/stream paths. |
| Capture or output materialization failure/missing output | Command result and command-owned problem at capture/materialize stage; preserve partial output availability and attempt classification. |
| Dependency skip / `dependency_failed` | Blocked command result with prerequisite references; affected artifacts are Not compared / Command blocked. Do not manufacture another root diagnosis. |
| Equal/changed comparison | Artifact result Matched/Not matched, original profile/fingerprints/evidence observations; inequality has an artifact-owned bounded difference diagnosis. |
| Comparator/evidence error, unsupported format or comparison resource failure | Artifact result Not compared / Comparison failed and artifact-owned problem retaining caught error type/message and available selector/member/row/key/shape observations. Equality/tolerances do not change. |
| Graph/depth/resource limit or safety failure | Retain the existing local-vs-operational detection boundary: a local prerequisite diagnosis or an operation failure, never a blanket remapping based on the code alone. |
| `stop_requested`, worker survived/cleanup incomplete, journal/callback failure | Private durable job lifecycle and operational diagnosis; no invented normally published command result. Resume/attempt/cleanup rules remain unchanged. |
| Cumulative artifacts/current-run buckets/count reconciliation | Immutable per-run work/results/problems plus minimal latest identity indexes for retry/reuse. Saved inspection never reprojects current topology. |

Planning and admission own current work and preparation problems.
`reproduction_work_plan`, accepted storage and `reproduction_work_job`
own immutable acceptance and genuine operational state. Physical execution and
the original profile comparators own runtime observations.
`reproduction_saved_storage` owns saved reproduction DDL/history;
publication, inspection and report rendering consume those accepted and durable
facts. There is no cumulative issue projection, adapter registry, extra causal
graph or public problem browser.

### Exact Synthetic Presentation Examples

These examples fix expected presentation independently of the renderer. They
are synthetic, not maintained research outcomes. Text uses the final log
directory name and compact UTC month/day; JSON retains canonical full identities
and timestamps. Explicit zeroes are not omitted. Reason labels are human-readable
forms of the exact mechanical code; filtering still uses the code.

The mixed saved fixture has seven commands: one of each Not run child, one
policy exclusion, one success, one failed producer and its blocked consumer.
The successful command has equal and unequal outputs; the other two artifacts
were not compared. The consumer reaches the producer's one execution diagnosis
through its prerequisite reference; it does not carry a copied diagnosis ID.

```text
Log: study
Run: reproduce-mixed
Saved: Sep 15
Target: Log
Status: Complete

Commands — 7
  Not run — 3
    Not needed — 1
    Previous failure — 1
    Previous block — 1
  Skipped by policy — 1
  Selected — 3
    Succeeded — 1
    Failed — 1
      Execution failed — 1
    Blocked — 1
      Execution failed — 1

Artifacts — 4
  Matched — 1
  Not matched — 1
  Not compared — 2
    Command blocked — 1
    Command failed — 1
```

The corresponding current-plan fixture instead has the producer's unavailable
script disclosed before acceptance: one ready command and two blocked commands,
with the same three Not run leaves and policy exclusion. Artifact total is four;
the preview contains no predicted Matched/Not matched counts. Its first page is
the complete five-item attention list (Ready, Blocked, Previous failure/block),
with returned 5, remaining 0 and cursor null. The blocked consumer's explanation
points to the producer's known unavailable script, not a fabricated new defect.

```text
Log: study
Target: Log
Commands — 7
  Ready to run — 1
  Blocked — 2
  Not needed — 1
  Previous failure — 1
  Previous block — 1
  Skipped by policy — 1
Artifacts — 4
```

Command detail for the failed producer leads with `Failed — Producer exited
with status 2.` It retains exact entry/CID/execution identity, the recorded
`python scripts/producer.py` invocation and working directory, declared
inputs/outputs, stage Execute, code `execution_failed`, observed return code 2,
original timing and stdout/stderr locations. The consumer detail leads with
Blocked and the same diagnosis plus its producer prerequisite reference; it
does not imply the consumer ran. Missing stream files are explicitly unavailable
while the retained return-code diagnosis stays visible. A cause with no recorded
exception type does not acquire an invented exception type.

Artifact detail for `e001/data/unequal.txt` leads with `Not matched — Line 3
differs.` It identifies the successful producing command, retained and regenerated
paths, Text comparison profile, expected/regenerated fingerprints and the retained
observation `line: 3, expected: "2", actual: "3"`. A missing regenerated file
qualifies availability, not the stored mismatch or its line/value diagnosis.

Missing saved results have no run, target counts or invented zero outcomes:

```text
Log: study
Saved results: Unavailable
Reproduce this log to create saved results.
```

A confirmed empty saved log has complete identity/run/date/status and zero in
every command/artifact leaf. In a root view containing the mixed log and that
empty log, the two tables are exactly:

| Log | Saved | Target | Total | Not run | Policy | Succeeded | Failed | Blocked |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| study | Sep 15 | Log | 7 | 3 | 1 | 1 | 1 | 1 |
| empty | Sep 15 | Log | 0 | 0 | 0 | 0 | 0 | 0 |
| Total | | | 7 | 3 | 1 | 1 | 1 | 1 |

| Log | Total | Matched | Not matched | Not compared |
| --- | ---: | ---: | ---: | ---: |
| study | 4 | 1 | 1 | 2 |
| empty | 0 | 0 | 0 | 0 |
| Total | 4 | 1 | 1 | 2 |

Entry-targeted runs replace Target Log with their exact entry; they do not become
whole-log results. Missing/error rows use em dashes and are disclosed below the
tables; aggregates include only available recorded targets and are qualified
when coverage is incomplete. `reproduction.md` adds only its Reproduction heading
to the same single-log saved summary, without these detail inventories/examples.

### Inspection Bounds, Cursors And Errors

Default saved inspection selects the latest complete run in descending finished
time, then descending accepted time, then descending run ID. Explicit `--run-id`
selects only that immutable run, never a cumulative view or a different entry's
history. The native saved-store boundary owns this ordering for single-log and
root inspection.

Accepted plan and saved-run JSON retain the existing 64 MiB bound. Each work,
result or problem collection has a fixed 100,000-record ceiling, with linear
problem construction measured separately from necessary output/dependency
links. A complete problem diagnosis is bounded at 16 KiB. Stream excerpts retain
the existing 16 KiB per-stream tail; a complete inspection response is bounded
at 128 KiB. Collection sections/pages contain at most 50 items, with exact
matched/returned/remaining counts and navigation. These are fixed contracts,
not corpus-sized or configurable limits. Exceeding a scalar/record byte bound
fails closed rather than silently truncating the diagnosis. Stream or collection
excerpt truncation is explicitly disclosed and full retained paths are supplied.

Recorded relative stream paths resolve beneath the accepted run's canonical
dated directory, using saved coverage, acceptance time, run ID and project root.
The existing project `tmp` symlink contract applies; inspection does not scan
other runs or current registries. Absolute stream paths must lie inside that
same logical or resolved run directory. Stream-component symlinks are rejected.
Missing retained files affect availability only, never saved classification.

Replacement-model report recovery renders only `# Reproduction` followed by
the shared saved single-log `show` body. Under the reproduction-publication
and shared-results locks, it writes the report and advances its materialization
marker for the exact saved generation. A failed write or marker update leaves
saved facts queryable and recovery retryable. Recovery never executes, replans,
or reads live inventory or registries. Normal publication uses the same renderer.

The opaque URL-safe cursor is bounded at 2 KiB and contains exactly `schema`,
`kind`, `binding`, `offset`; schema is `research-log-reproduction-cursor/1`, kind
is `commands`, `artifacts` or `plan`, binding is SHA-256, and offset is a
nonnegative integer, not boolean. Saved-list binding includes canonical log,
run ID, reproduction generation, kind, every filter and format. Plan binding
includes the freshly evaluated complete source identity, target, every policy
and runtime setting and format. A continuation reruns read-only preparation;
it does not consult a saved preview cache. Changed bindings are stale errors,
not mixed pages. Summary totals always describe the complete target.

Public errors use `reproduction.results.missing`,
`reproduction.results.unsupported`, `reproduction.results.invalid`,
`reproduction.run.unknown`, `reproduction.selector.invalid`,
`reproduction.identity.unknown`, `reproduction.cursor.invalid`,
`reproduction.cursor.stale`, `reproduction.limit.exceeded`. Missing results are
unavailable, not zero. A supported confirmed empty target has explicit zeroes.
Unsupported reads are nonmutating and direct the researcher to explicit recheck.
Missing retained diagnostics are availability fields, not a different outcome.

## Versioned Surfaces

| Surface | Current version |
| --- | --- |
| Execution state | `research-log-pyrun/v7` |
| Execution identity | `pyrun-exec/v2` |
| Standard environment | `pyrun-standard/v1` |
| Execution contract | `research-log-pyrun-execution/2` |
| Accepted plan | `research-log-reproduction-plan/14` |
| Saved run | `research-log-reproduction-result/14` |
| Durable job | run-local `state.sqlite`, user_version 6 |
| Shared results | `<log>/.cache/results.sqlite`, user_version 19 without reproduction, 22 with current reproduction; earlier reproduction formats are unsupported and replaced only by explicit recheck |
| Operational status | `research-log-reproduction-status/7` |
| Project scheduler | `reproduction-scheduler.sqlite`, user_version 2 |
| Comparison | `research-log-reproduction-comparison/1` |
| Evidence comparison | `research-log-evidence-scoped-comparison/1` |
| Evidence detail | `research-log-evidence-scoped-comparison-result/1` |
| Isolated verification | `research-log-command-verification-result/1` |

Schema and comparison versions do not incidentally alter execution identity.

## Fixed Resource Bounds

These limits are code-owned v1 constants. They are not authored metadata or
CLI settings. A decoder rejects an over-limit durable file. Planning,
execution, comparison, and publication fail explicitly on an over-limit
operation; they never truncate, sample, or silently narrow it.

Work/result/problem and inspection bounds additionally apply as defined in
[Inspection Bounds, Cursors And Errors](#inspection-bounds-cursors-and-errors).
Limits are fixed contracts, not dynamically chosen from the current corpus.

### Execution State And Serialization

| Resource | Limit |
| --- | ---: |
| `pyrun.json` encoded bytes | 16 MiB |
| Executions per entry | 256 |
| Parameters per execution | 4,096 |
| Direct inputs per execution | 128 |
| Outputs per execution | 256 |
| Explicit environment variables per execution | 64 |
| Effective-code source files per analysis | 256 |
| Bytes per effective-code source file | 1 MiB |
| Reported unsupported effective-code locations | 64 |
| Bytes per ordinary string | 8 KiB |
| Bytes per normalized path | 2 KiB |

### Planning And Graphs

| Resource | Limit |
| --- | ---: |
| Reachable executions per log target | 2,048 |
| Reachable artifacts per target | 10,000 |
| Total graph nodes | 16,384 |
| Total graph edges | 32,768 |
| Dependency depth | 64 |
| Boundaries per plan | 10,000 |
| Owned preparation problems per plan | 100,000 (native work-record ceiling) |
| Accepted plan encoded bytes | 64 MiB |

An entry target uses the same ceilings but cannot traverse a command outside
the selected entry. Graph limits do not authorize broader scope.

### Durable Runs, Workers, And Staging

| Resource | Limit |
| --- | ---: |
| Durable run `state.sqlite` data plus safe `-journal`, `-wal`, and `-shm` companions | 256 MiB combined when companions are present |
| Status projection encoded bytes | 64 MiB |
| Registered workers per execution | 1,024 |
| Registered workers per run | 4,096 |
| Project scheduler SQLite data | 64 MiB |
| Active project scheduling permits | 4,096 |
| Waiting project exclusive tickets | 10,000 |
| Checkpoints per run | 2,048 |
| Outputs per checkpoint | 256 |
| Command-query excerpt per diagnostic stream | 16 KiB |
| Structured diagnostic events per run | 1,000,000 |
| Structured diagnostic bytes per run | 1 GiB |
| Runner-owned temporary and staging bytes per run | 1 TiB |
| Graceful stop interval | 30 seconds |
| Forced-stop verification interval | 10 seconds |
| Default command runtime | 300 seconds |
| Maximum configurable command runtime | 604,800 seconds |

The storage ceiling supplements, and does not replace, a preflight check for
adequate available project-local space. A stop interval bounds one cleanup
stage; it does not permit publishing `stopped` while a worker survives.

### Comparison

| Resource | Limit |
| --- | ---: |
| Encoded bytes per regular artifact | 1 TiB |
| Directory members | 100,000 |
| Directory nesting depth | 64 |
| Aggregate directory content bytes | 1 TiB |
| JSON nesting depth | 256 |
| JSON logical nodes | 10,000,000 |
| Table rows | 10,000,000 |
| Table columns | 10,000 |
| Table cells | 100,000,000 |
| Logical array members | 17,179,869,184 |
| Decoded image pixels | 2,147,483,648 |
| Comparator working memory | 4 GiB |

Streams, iterators, chunked decoders, and memory maps must enforce logical
limits without first allocating the bounded maximum. Nested container members
also consume the ordinary file, path, and directory limits.

## Authority And Boundaries

### Reproduction Authority

The operational authority is:

| Surface | Authority |
| --- | --- |
| Research-log Markdown | Human research account, evidence presentation, and explanatory command history |
| `evidence.json` | Reproduction roots and exact retained evidence-source identity |
| `data.json` | Named material location, declaration identity, and origin/generated classification |
| `pyrun.json` | Current executable recipes and observed execution state |
| `.cache/results.sqlite` validation domain | Retained validation snapshots and report projection; Reproduce does not read this domain for admission |
| `validation.md` | Source-controlled human validation projection only |
| `.cache/results.sqlite` reproduction domain | Disposable local reproduction results and run projection |
| Durable run directory and `state.sqlite` | Active, stopped, failed, and staged run-specific operational state |
| `reproduction.md` | Source-controlled human reproduction projection only |

`evidence.json`, `data.json`, and `pyrun.json` together are the complete
reproduction graph and execution authority. Reproduction must not derive case
selection, material relationships, recipes, parameters, environment, or
execution identity from Markdown. It must not fall back to Markdown when JSON
state is absent or inconsistent.

Mechanical validation must independently check Markdown and JSON and their
required structural, evidence, and provenance agreement. Disagreement is a
validation finding. Semantic review owns scientific meaning, relevance, and
narrative fidelity beyond those mechanical checks.

### Operation Boundary

Reproduction determines whether retained generated artifacts used by evidence
can be regenerated from recorded recipes and retained direct inputs. It does
not establish scientific validity, historical production, semantic agreement,
or independent replication.

The CLI owns discovery, planning, ordering, execution, comparison, durable
state, and publication. An agent must not select cases, infer dependencies,
judge equivalence, orchestrate child processes, or edit the machine records.

Reproduction may change only its generated state and the reproduction-
requirement field in `pyrun.json`. After a completed run, it does not invoke
validation; validation alone owns its generated state when separately requested. Reproduction
must not edit research prose, Markdown commands, evidence presentation,
`data.json` declarations, retained artifacts, or other human-authored log
content. Promotion is the separate researcher-directed exception for replacing
retained artifacts.

An ordinary fresh `pyrun` uses current material selected by `data.json` and,
after a stable successful publication, records those observations in its own
execution. It must not refuse merely because another execution observed older
bytes, and it must not certify an altered upstream producer or rewrite any
other execution. Reproduction instead confirms one recorded execution against
its retained inputs, effective code, and outputs; altered retained material
rejects confirmation. Promotion publishes a selected regenerated output set,
but neither promotes a new declaration identity nor accepts a new evidence
artifact baseline. Evidence comparison and provenance are independent: a
matching presentation does not make an execution current, and current fresh
execution does not make an older presentation accepted.

## `pyrun.json`

### Ownership And Location

Each entry root may contain one generated `pyrun.json`. `pyrun` and its
explicit lifecycle services own it. Researchers and agents must not edit it
directly. An entry with no current execution state omits the file.

The file records current executable state, not attempts or history. It has no
output index, comparison policy, reproduction timestamps, failed attempts, or
superseded recipes.

Before execution, `pyrun` holds the entry-operation lock and validates current
state. A malformed regular `pyrun.json` is preserved at the first unused
`pyrun.json.bak`, `pyrun.json.2.bak`, or later numbered backup. The runner reports
`pyrun.state.quarantined` with the backup and `repair_required:true`, then exits
without executing or creating replacement state. A symlink or non-file is
rejected without quarantine. A legacy `pyrun-outputs.json` blocks another run;
the runner does not convert it. Its separate read-only compatibility contract
is defined in [Legacy Output Records](research-log-mechanical-validator-spec.md#legacy-output-records).

### File Shape

The file is strict UTF-8 JSON with no duplicate keys, non-finite numbers,
unknown fields, or trailing content, and with one trailing newline. It has
exactly:

```json
{
  "schema": "research-log-pyrun/v7",
  "commands": {
    "build-results": {
      "executions": {
        "pyrun-exec/v2:0123456789abcdef...": {
          "auto_reproduce": true,
          "exclusive": false,
          "last_run_at": "2030-01-01T00:00:00Z",
          "requires_reproduction": false,
          "runner": "research-log-pyrun-runner/1",
          "environment_profile": "pyrun-standard/v1",
          "execution_contract": "research-log-pyrun-execution/2",
          "recipe": {
            "script": "scripts/run_study.py",
            "parameters": [
              "--input-data",
              "<catalog>",
              "--output-csv",
              "data/results.csv"
            ],
            "parameter_roles": {
              "input-data": "input",
              "output-csv": "output"
            },
            "environment": {},
            "inputs": ["catalog"],
            "outputs": {
              "data/results.csv": "file",
              "images/results.png": "file"
            }
          },
          "observed": {
            "script": {"algorithm": "sha256", "digest": "..."},
            "inputs": {
              "catalog": {"algorithm": "sha256", "digest": "..."}
            },
            "effective_code": {
              "algorithm": "python-effective-code-sha256-v1",
              "digest": "..."
            },
            "outputs": {
              "data/results.csv": {"algorithm": "sha256", "digest": "..."},
              "images/results.png": {"algorithm": "sha256", "digest": "..."}
            }
          }
        }
      }
    }
  }
}
```

Top-level keys are exactly `schema` and `commands`. Command-map keys are stable
entry-unique CIDs, and each command has exactly one `executions` map whose keys
are unique parameter execution IDs. Every execution value has exactly `auto_reproduce`,
`exclusive`, `requires_reproduction`, `last_run_at`, `runner`, `environment_profile`,
`execution_contract`, `recipe`, and `observed`.

`auto_reproduce`, `exclusive`, and `requires_reproduction` are required Booleans.
`requires_reproduction` records whether execution state still needs successful
reproduction before ordinary incremental planning may use it without
reproduction-owned cache state. It may reflect historically reconstructed state
or a current explicit execution repair; it is not a data-declaration baseline.
`exclusive` states that reproduction must run the execution alone among all
managed reproduction executions in the current Git project. It is scheduling
policy, not a CPU, GPU, device, affinity, or external-process declaration.
`last_run_at` is either `null` or
a UTC RFC 3339 timestamp with whole seconds and `Z`. Historically reconstructed
execution state retains `null` when no ordinary `pyrun` completion time is
known; current observation must not fabricate one. Version fields are required
nonempty identifiers from the code-owned
supported sets.

`recipe` has exactly `script`, `parameters`, `parameter_roles`, `environment`,
`inputs`, and `outputs`:

- `script` is the normalized POSIX script argument. A script beneath the entry
  uses its entry-relative identity; any other script beneath the maintained log
  uses the inherited `<log>/...` identity; and a script elsewhere in the
  current Git project uses `<project>/...`. Scripts outside the project are not
  eligible.
- `parameters` is the exact ordered replay parameter vector. It contains each
  leading runner-owned stream-capture option and target, followed by `--`, then
  the exact ordered child-process argument tail after the script. Without a
  capture, it contains only that child-process argument tail. It contains no
  runner role declarations, `--auto-reproduce=false`, or explicit environment
  options.
- `parameter_roles` maps every valued script parameter to its effective `input`,
  `output`, or `ordinary` role, including roles inferred from naming. Keys use
  the runner selectors: names without dashes and one-based `@N` positions.
  Repeated named parameters share one role. Flags without values and runner
  captures have no entries. Missing, extra, or invalid roles are rejected.
  Registered material tokens cannot be ordinary values.
- `environment` maps each explicit normalized `--env NAME=value` variable name
  to its exact value. It contains no inherited or runner-supplied variable.
- `inputs` is the sorted unique list of directly consumed `data.json` names.
- `outputs` maps every declared output identity to `file` or `directory`.
  Entry-owned outputs use their normalized entry-relative path. Outputs
  elsewhere in the current Git project use the inherited normalized
  `<project>/...` identity.

Every declared output derives exactly one replay binding from these existing
fields; no binding field is persisted. A binding is either one runner-owned
capture target in the leading capture prefix or one complete child-parameter
value, including the value after the first `=` in an equals-delimited option.
The same canonical output in more than one occurrence is ambiguous, even if
only one occurrence carried an output role during ingestion. An output in the
map with no occurrence is missing. Either condition is invalid for execution
and a Conformance finding during validation.

A single noncanonical spelling that resolves to the output identity remains
mechanically bindable but is also a Conformance finding. This lets `pyrun`
record the completed live invocation without inventing a second identity while
requiring the authored command to use the canonical spelling before
reproduction. The binding projection is derived wholly from `parameters` and
`outputs`, both already covered by the execution identity.

`observed` has exactly `script`, `inputs`, `effective_code`, and `outputs`:

- `script` is the raw-byte fingerprint of the directly executed script. It is
  retained as exact-source provenance and diagnostic context, but never by
  itself selects reproduction or clears a reproduction requirement.
- `inputs` maps every recipe input name to its execution-time fingerprint.
- `effective_code` is either one
  `python-effective-code-sha256-v1` fingerprint or `null`. The fingerprint
  covers normalized syntax for statically reachable Python behavior from the
  direct script through project-local imports, functions, methods, values, and
  child Python entrypoints. Traversal may cross the whole current Git project
  and stops at its boundary. External package implementation does not
  participate. A method absent from a project-local class hierarchy terminates
  at that boundary when every base path is statically resolved and at least one
  reaches an external base; the call syntax and arguments remain part of the
  local fingerprint.
  Comments, formatting, source positions, and unreachable local definitions do
  not participate.
- `outputs` maps every recipe output identity to its execution-time
  fingerprint.

The runner derives one import context for the command and gives it unchanged to
ordinary execution, verification, reproduction, and analysis. Resolution is
ordered from the executing script's directory through the owning entry's
`scripts/`, the owning log's `scripts/`, and the project root, with duplicate
roots removed. These roots are private runner state: they are neither authored
environment nor persisted metadata. Explicit authored `PYTHONPATH` remains
unsupported because it changes this resolution contract.

The analyzer parses source; it never imports or executes project code. Dynamic
imports or generated code, source-level runtime import-path mutation, wildcard
imports, unresolved dispatch whose complete base hierarchy cannot be proved to
terminate externally, and unresolved child Python entrypoints are unsupported. Unsupported analysis
never emits a partial fingerprint or a whole-module fallback: `effective_code`
is `null`. Ordinary `pyrun` remains silent and executes. Command sync succeeds
for unsupported and operationally failed analysis but reports bounded
structured warnings with the affected location and the consequence that code
currentness is unavailable until the source is made analyzable and `pyrun` is
run again. Reproduction remains runnable and repeatedly selected while the
fingerprint is unavailable. Operational analysis failures remain errors to
ordinary `pyrun` publication. During Reproduce preparation,
any analysis result without a fingerprint selects runnable work with its exact
diagnosis as long as the top-level script itself can be frozen.

A commit-pinned `git-repository` input remains a `data.json` origin. Its recipe
input is still the data name, and its observed value uses the inherited exact
repository-and-commit fingerprint form. Reproduction resolves and verifies the
recorded commit; it must not substitute the current checkout or a branch tip.

For a confirmed execution whose `requires_reproduction` is false, the recipe
and observed input/output key sets agree exactly and `script` is present. A
record requiring reproduction may contain a subset of still-applicable input
and output observations and may set `script` or `effective_code` to null.
Missing saved effective code and current code that cannot be fingerprinted both
select runnable work with the distinct `effective_code_unavailable` diagnosis.
Unfingerprintable current code is selected on every incremental plan because
unchanged currentness cannot be established; saved history does not suppress
it. Missing historical observations are unavailable history, never current
evidence or a match. Every fingerprint uses the closed
forms owned by the mechanical validator specification. `data.json` remains the
sole owner of input paths, classifications, and identity selection;
`pyrun.json` owns the historical observations that reproduction compares.

The fixed file, execution, parameter, string, input, output, environment, and
effective-code analysis limits are defined in
[Fixed Resource Bounds](#fixed-resource-bounds).
Exceeding a bound is invalid state; readers must not truncate it.

### Execution Identity

An execution ID has the form `pyrun-exec/v2:<digest>`, where `<digest>` is the
lowercase hexadecimal SHA-256 digest of the canonical expanded child-parameter
vector. For example, the empty parameter vector projects as:

```json
[]
```

It uses compact UTF-8 JSON with no ASCII escaping or trailing newline and
retains every token boundary and array position. The vector contains only the
expanded child-script parameters after the script. It excludes CID, script,
runner options and captures, environment, parameter roles, declarations,
observations, policies, timestamps, locations, and version fields.

Changing script bytes or direct-input bytes makes observed state stale without
changing the execution ID. Changing the expanded parameter vector changes the
ID. Changing script path, environment, roles, declarations, captures, or policy
preserves the parameter ID but is detected by full-recipe or policy comparison.
Equal parameter IDs under different CIDs remain distinct complete keys.

### Eligible Invocation

Only `pyrun` may establish reproduction-eligible execution state. Each eligible
fence contains exactly one authored command or bounded static loop. A command
with no runner options may pass a Python program directly. When runner options
are present, they form one leading group followed by `--` and the program.
`--cid VALUE` is one such runner option.

A nonnumeric authored value is the full CID and overrides derivation. A
canonical positive integer `N` resolves to the full effective CID formed from
the valid lexical Python filename stem plus `-N`; for example,
`--cid 2 -- scripts/foo.py` resolves to `foo-2`. Zero and leading-zero forms
are invalid. Without `--cid`, the program must be a `.py` path whose lexical
filename stem satisfies the command-ID grammar: one ASCII alphanumeric
followed only by ASCII alphanumerics, `_`, or `-`. The effective CID is unique
within the entry across split documents, is shared by every concrete expansion
of its command or loop, and never reaches the child process. Numeric shorthand
is available for repeated uses and basename collisions with a valid Python
stem. A full authored CID is required for non-Python programs, invalid stems,
multi-program owners, and preservation of an existing identity when the
program filename changes. In the last case, changing `foo.py` to `bar.py`
while retaining the existing identity requires `--cid foo`.

`pyrun.json` stores only the resolved effective CID as its ordinary
command-bucket key. It does not record the authored token, whether the CID was
authored or derived, or any separate invocation number. This behavior does not
change the state schema.

Production command blocks must not use direct non-`pyrun` executables,
pipelines, redirection, `tee`, shell environment prefixes, command or process
substitution, dynamic shell discovery, or other general shell interpretation.
Equivalent behavior must use a `pyrun` facility or a retained Python wrapper.
A retained Python wrapper may invoke any required external executable.

Runner-visible input and output roles are ingestion metadata. Once `recipe`
and `observed` contain explicit associations, reproduction neither stores nor
replays those role declarations.

### Standard Environment

Reproduction uses the current project's `.conda/bin/python`. The entry-local
`./pyrun` launcher selects that interpreter before loading its implementation
when it exists; its documented authoring fallback to a caller-available
supported `python3` does not satisfy the reproduction environment contract.
The interpreter fingerprint, installed-package inventory, and complete
inherited process environment are outside the contract.

The versioned standard environment supplies runner-controlled temporary
`MPLCONFIGDIR` and `XDG_CACHE_HOME` locations. Qualified external runtimes
receive equivalent runner-owned preference locations where required. Concrete
temporary paths are not serialized.

Repeatable `--env NAME=value` options are normalized into `recipe.environment`
and participate in complete-recipe comparison, but not the parameter-only
execution ID. A missing project environment or required
executable prevents reproduction. Environment drift that still executes and
changes output is reported through artifact comparison rather than diagnosed
by inference.

### Atomic Publication And Replacement

One execution owns one complete unconditional output set. Output paths within
an execution must not duplicate, alias, overlap as file and directory, or have
an ancestor-descendant relationship. Each current output has exactly one
current execution owner.

Ordinary `pyrun` publishes only after:

1. the child exits successfully;
2. the direct script bytes and inputs remain stable, and a supported
   effective-code fingerprint remains equal before and after execution;
3. every declared output exists with the declared kind and can be observed
   completely; and
4. the new execution passes the production decoder, identity, and output-set
   checks, and its outputs do not overlap any other owner in the validated
   freshly read publication state.

Unsupported effective code remains null and does not block ordinary `pyrun`.

A successful identical recipe atomically replaces its observed state. A
successful new recipe whose output set overlaps another execution owner is
rejected without deleting or reassigning existing state.
Failed or incomplete execution, capture, observation, or publication changes
no `pyrun.json` state.

The common single-file publication primitive owns temporary-file cleanup,
existing-mode preservation, atomic installation, and directory sync for
`pyrun.json` and other research-log files. Execution-state validation and
ownership remain with the `pyrun.json` writer. Multi-output promotion keeps its
operation-owned displacement and rollback sequence; it is not delegated to a
generic transaction layer.

Ordinary `pyrun` strictly reads existing state in a short entry transaction,
then hashes and executes without holding entry/log OS locks. It verifies the
selected Markdown command, participating data declarations, and recorded recipe
authority before launch and again in the short completion transaction. Relevant
changes reject execution/publication with a review-and-rerun diagnostic. Completion
reloads validated state and merges only the successful execution, preserving
other commands' concurrent updates. A concurrent change to that execution is
rejected rather than overwritten. Script/input/helper observations are rechecked
unlocked after reservation and after execution. Direct edits do not bypass
these checks or become an ordinary workflow.

### Ordinary Artifact Reservations

An ordinary invocation reserves its declared physical read/write boundaries
under a brief project-local `artifact-reservations.lock`. Readers may share an
input; a writer excludes overlapping readers/writers, including directory
ancestors/descendants and capture files. The worker registers its process group
before executing user code. No OS lock spans execution. A conflict reports
`artifact.reservation.conflict`, entry/CID, paths, and process owners. Authoring
must not relocate or change the identity of a boundary in use. Reservations do
not stage outputs or roll back bytes written by a failed script.

Generated reservation records live in the owning project's
`.cache/research-log-operations/ordinary-execution-UUID.json`. Their schema is
`research-log-artifact-reservation/1`; fields are `schema`, 32-character lowercase
hex `identity`, absolute `entry`, full `cid`, absolute path arrays `reads` and
`writes`, positive integer `parent_pid`, and nullable positive integer
`worker_pid`. Readers reject malformed records, records over 64 KiB, or more than
1,000 records. A successful/failed launcher removes its reservation only after
its worker group is gone. Killing the launcher does not unprotect a surviving
worker. Detached work escaping that group is not a supported script lifecycle;
scripts finish every consumer before returning.

Abandoned reservations never expire automatically. Explicit cleanup is:

```text
log command release [--path LOG] --entry ENTRY --cid CID [--dry-run]
```

It selects only that entry/full CID, refuses any live parent or worker group,
and removes only abandoned generated reservation records. Dry-run writes no
content. It does not edit execution state or retained artifacts, certify
partial outputs, or grant permission to rerun the research. PID reuse is treated
conservatively as a live owner. Validation/reproduction locking and scheduling
contracts remain unchanged.

### Ordinary Publication Metadata

`last_run_at` records the completion time of the latest successful atomic
ordinary `pyrun` publication. Historically reconstructed state retains `null`
until a later ordinary publication establishes such a time. Failed
attempts, reproduction execution, and reproduction-requirement or policy-only mutations
must not change it. Ordinary `pyrun` reads and writes only its entry-local
state; it must not load, scan, mark, or rewrite log-wide reproduction results.

An ordinary successful publication records `requires_reproduction: false`.
Historically reconstructed state that has not completed a successful execution records
`requires_reproduction: true` and remains runnable. Reproduction changes the
field to false immediately after the command reaches its complete mechanical
endpoint and its complete comparison is durably recorded. Artifact matching is
separate: a completed command clears the requirement even when an artifact is
changed or its comparison fails. If every artifact is canonically matched,
reconciliation also records the plan's accepted raw-script and effective-code
observations, including a null effective-code observation. A null observation
does not establish currentness and therefore remains selected by later plans.
If any artifact is unequal or uncomputed, reconciliation preserves the prior
observations. The mutation
preserves the recipe, inputs, output baselines, policy, versions, and
`last_run_at`; later work, result
publication, and validation do not restore the requirement. A failed, blocked,
or stopped command leaves it true.

### Automatic-Reproduction Policy

Placing `--auto-reproduce=false` in the runner-option prefix records
`auto_reproduce: false`. Omitting the option records `auto_reproduce: true`.
The false value marks work that must not be rerun automatically, such as
simulation or model training. It is an authored policy, not an inference from
measured duration. No other spelling or truthy/falsey value is accepted.

Automatic-reproduction policy is outside identity. For a later policy-only
change, edit the Markdown option first and synchronize its CID:

```text
log command sync --path LOG --entry ENTRY --cid CID
```

Sync derives policy from every current expansion, preserves applicable
observations and reproduction requirements, and never edits Markdown or runs
the recipe.

### Exclusive-Scheduling Policy

Placing `--exclusive` in the runner-option prefix records `exclusive: true`.
Omitting it records `exclusive: false`. No value-bearing or negative spelling
is accepted. The option affects managed reproduction scheduling only: ordinary
direct `pyrun` execution is unchanged, and the runner does not reserve CPUs,
GPUs, devices, host affinity, or unrelated host processes.

Exclusivity is outside execution identity. Apply a later Markdown-first policy
change with the same `log command sync --path LOG --entry ENTRY --cid CID`
operation. Sync applies the authored policy to every current expansion without
merging their parameter identities.

### Markdown-First Command Synchronization

Edit the recorded Markdown command first, then run. `--path` may be omitted
only when the working directory has exactly one maintained ancestor log;
otherwise specify the logical log base to resolve missing or ambiguous context.


```text
log command sync [--path LOG] --entry ENTRY
  [--cid CID]... [--rename OLD=NEW]... [--delete CID]...
  [--delete-stale-executions CID]... [--dry-run]
```

Sync reconciles every current parameter expansion in each selected CID. It
preserves unchanged records, retains only applicable observations when a recipe
changes, applies policy-only changes without changing reproduction state, and
creates observation-empty pending records for missing expansions. Stale
parameter identities require `--delete-stale-executions CID`, which retires
all stale members of that selected CID. Dry-run reports the exact members.
Redundant acknowledgement of a CID with no stale members is accepted.

Supply missing file declarations in the same transaction with repeatable
`--add-origin NAME=PATH` or `--add-generated NAME=PATH`; directory variants
explicitly declare directories. Git origins use `--add-origin-git NAME=COMMIT:PATH`
and cross-entry references use `--add-from-entry NAME=ENTRY`.
Local target changes use `--change-target NAME=TARGET` only when all command
consumers are selected and no evidence or cross-entry consumer exists. Command
renames and deletions use `--rename OLD=NEW` and `--delete CID` after editing
Markdown. Material-name identity changes, shared or advanced target changes,
and material-name deletion remain under `log data`.

The operation validates complete `data.json` and `pyrun.json` candidates and
publishes both atomically under short entry and log locks. It never samples script, input,
or output bytes. `--dry-run` performs the same semantic checks and returns both
complete unified diffs without writing registries, diagnostics, or caches.

### Execution-Metadata Schema

Entry-local execution state accepts only strict `research-log-pyrun/v7`.
Current command records require the complete `parameter_roles` map. Their
execution IDs and source digests support fresh incremental selection only; they
are not execution authority for current state. Resume reloads the immutable
accepted plan and performs only invocation-scoped observation checks.

### Retirement

Retirement removes one complete execution and all of its output support through
the owning lifecycle service. It is permitted only after proving that no
maintained evidence or downstream generated-data dependency requires any
output without another valid producer. Retirement must be explicit and
researcher-approved; no migration or cleanup path may infer it.

## Current Contract Cutover

The current data and evidence readers accept only `research-log-data/v6` and
`research-log-evidence/v5`. Their conversion from data/v5 and evidence/v4 is
a one-time plan-owned operation using disposable tooling, removed before plan
completion. No data/evidence migration CLI, legacy decoder, compatibility
reader, or conversion tooling remains in the final runtime. Reusable docs and
tests cover only the current data/evidence contracts; historical conversion
fixtures, if needed, belong only to that disposable conversion work.

Execution state was replaced from v6 to v7 by a one-time, plan-owned migration.
That migration preserved every recipe, policy, timestamp, raw script, input and
output observation; replaced each per-file code map with a current nullable
effective-code fingerprint; and set every execution to
`requires_reproduction: true`. After the runner-owned import context and
maintained-script cleanup were complete, the same plan-owned utility refreshed
all 51 Girmos registries and 1,029 executions: 1,029 supported observations and
zero unsupported observations, with all 1,029 still requiring reproduction. No
maintained reproduction was launched. Ordinary runtime readers accept only v7;
there is no v6 compatibility reader or runtime migration path.

The current reconciliation boundary advances accepted plans and saved runs to
v14, durable jobs to user version 6, and the shared reproduction domain to user
version 22. Earlier saved reproduction rows and jobs are not
decoded, translated, or resumed. Validation and command-diagnostic domains in
a version-20 shared store remain readable; explicit
`log reproduce run --path LOG --recheck` replaces only the obsolete
reproduction domain.

Mechanical validation separately retains the read-only
[Legacy Output Records](research-log-mechanical-validator-spec.md#legacy-output-records)
path for `pyrun-outputs.json` when no current file exists. That reader grants no
execution or conversion authority. A current observation may not be copied
into retained execution state as proof of an old run, and no action
reconstructs historical locator or classification state.

There is no legacy output projection or targeted-refresh evaluator. Fresh
execution, reproduction, and promotion preserve the distinction between current
observation, retained execution baseline, and evidence presentation baseline.

## Discovery And Planning

Discovery selects maintained logs using the ordinary project registry; it does
not itself validate or reproduce. Fresh preview and launch target exactly one
log or entry and use the same typed planner after a complete fresh mechanical
evaluation. Preview is read-only. Launch holds normal preparation/publication
locks briefly, accepts immutable work, then releases them before execution.

Inventory includes every recorded execution in the target. Evidence reachability
and complete declared output traversal preserve the existing artifact inventory.
Origin inputs are verified retained boundaries. An entry target cannot schedule
a producer outside that entry; a verified external producer remains boundary
context. A whole-log target does not import commands from another log.

Admission binds each expanded execution to its recorded command and the shared
research graph. Applicable blocking validation findings exclude only their
owned work; orphan findings and reproduction-required observations do not
block reproduction. Excluded work retains the exact finding diagnosis.
Ambiguous/missing producer and direct input/source guards remain local causes.
Dependency cycles and failed prerequisites block related work; independent
components remain eligible.

Selection precedence is not-needed before automatic policy. Incremental work
runs when reproduction is required or currentness has changed, except an
unchanged prior failed, blocked, or completed-but-unreconciled source closure is
not retried. `--include-all`
allows nonautomatic work but does not independently retry an unchanged failure.
`--recheck` retries current work without bypassing policy; use both flags when
explicitly reproducing all recorded recipes. Required upstream changes propagate
to downstream selected work in deterministic dependency order. A supported
effective-code mismatch, a missing saved fingerprint, or current effective code
that cannot be fingerprinted selects runnable work. Preparation freezes the raw
script and nullable effective-code observation for pre/post execution stability
checks. A changed raw script fingerprint alone does not select work.
Unfingerprintable code bypasses unchanged-history suppression and is selected
on every incremental plan because its currentness cannot be proved;
comparison definitions and baseline observations qualify comparison reuse, not
execution success. Saved inspection never recomputes this live currentness.

## Execution Safety

### Run-Local Output Workspace

Every job executes retained scripts and project-local effective code directly from their
current verified locations under read-only confinement. It does not copy the
project. Each run owns an initially empty output workspace that mirrors only
the project-relative, log-relative, and entry-relative directories required by
generated paths.

Every ordinary declared output must bind unambiguously to exactly one recorded
child-parameter occurrence. Runner-owned captures are direct bindings. Before
execution, the executor substitutes each binding with the corresponding path
inside the run workspace. Blocking output-binding Conformance findings attached
to a selected execution exclude that execution and therefore prevent a recipe
with a missing, ambiguous, or noncanonical binding from reaching execution.
Unrelated selected work remains eligible. The executor consumes the same
shared binding projection defensively; an unexpected projection failure means
an accepted invocation observation changed or an implementation invariant failed,
not a separate artifact outcome or user-facing binding check.

The executor resolves retained origins and boundaries directly from their
verified read-only locations. When a downstream input is the output of an
earlier selected execution, it substitutes the regenerated path from the same
run workspace. Comparison reads the retained artifact as its immutable
baseline. No retained input or baseline is copied into the workspace.

Both ordinary `pyrun` and reproduction pass absolute paths for declared file
and directory inputs and outputs. Ordinary output bindings resolve relative
targets against the entry, even when launched from a nested working directory;
project output identities and captures retain their existing bindings.
Reproduction substitutes regenerated files and directory members exactly and
fails when required regenerated material is absent. It never falls back to old
material. Scalar arguments, including commit projections, and recipe identity
are unchanged by runner substitution.

Scripts consume the supplied paths without source-entry or working-directory
assumptions and pass them to helpers and children. A path embedded in input
contents must not select an additional file: expose that file as an explicit
declared parameter, using existing directory/member declarations where
appropriate. Stored paths may remain descriptive metadata. Missing declarations
and helper imports are script repairs under existing mechanisms; there is no
embedded-path resolver or code-discovery framework. Scripts relying on relative
output argument spelling require repair. Direct Python execution gains no
runner resolution or recording behavior.

Recipes execute from the workspace's mirrored entry directory using the
project-local Python, recorded environment, and the same runner-owned import
context used for analysis. Each attempt receives distinct runtime cache and
diagnostic roots. `PYTHONPYCACHEPREFIX`, `MPLCONFIGDIR`, `XDG_CACHE_HOME`, and
`MATLAB_PREFDIR` remain under its runtime root.

Direct execution and reproduction use the same bounded byte-copy, independent
destination-failure, pipe-drain, and source-close mechanics. Reproduction keeps
process-tree supervision, scheduling, deadlines, cancellation, and failure
precedence outside that shared component. Its retained stdout/stderr
diagnostics and declared captures are required destinations: a write, durable
flush, or bounded-drain failure stops the supervised tree and records
`capture_failed` unless incomplete worker cleanup has precedence. The isolated
command verification uses this same reproduction execution path without durable-job
state.

Both runners create fresh, unique scratch directories under `/private/tmp`
before launching each command and assign `TMPDIR` after authored environment
values. Children inherit it. Scripts use `tempfile` without a hardcoded root;
libraries and children may receive explicit paths created through `tempfile`.
Users and research agents supply no scratch path. Runner-added paths and
environment values do not enter recipe identity.

Scratch is separate from declared inputs/outputs, caches, diagnostics, and
checkpoints. Reproduction records its assigned absolute scratch path in the
identity-scoped checkpoint row before launch and clears that field only after
scratch cleanup succeeds. It is not a dependency or resume input.
Confinement permits the assigned scratch directory and its contents alongside
the attempt's run, runtime, and diagnostic roots, preserving read-only retained
boundaries and restrictions on unrelated paths.

Both runners remove scratch after success, failure, timeout, or stop once
workers finish. Ordinary scripts must finish children before returning.
Reproduction retains cleanup ownership while workers remain alive and blocks
reuse until workers stop and scratch is removed. Cleanup failures are reported;
scratch is never retained for debugging. Completed results may be reused under
existing resume rules, but every relaunched command receives empty scratch.
Abrupt runner or host termination can leave scratch behind. Recovery confirms
workers are gone and removes recorded reproduction scratch before relaunch;
it never treats leftovers as reusable state or sweeps unrelated ordinary
temporary directories.

For parallel execution, each attempt receives a distinct mirrored entry run
directory. Its run directory, declared output targets outside that directory,
capture targets, and private runtime and diagnostic roots form the accepted
`run_path`, `write_paths`, and `writable_paths` claims. Fresh scratch is private
to each launch and adds no shared scheduling claim. Regenerated dependencies
become read-only to consumers after their producer checkpoint is durable and
their exact accepted artifacts have matched. The supervisor performs atomic materialization
into shared dependency locations. Independent executions in the same entry may
run concurrently when their logical output, input, and writable claims do not
conflict; a resumed stopped attempt reuses its original run path and receives
new scratch. Retained scripts, project-local effective code, inputs, boundaries,
comparison baselines, and the project-local environment remain read-only.
A script that ignores a substituted output and attempts an unrelated write
fails at runtime; static inspection never substitutes for confinement.

### Network And External Effects

The complete reproduction process tree must run with network access denied.
Network denial and write confinement are runtime controls, not static promises
derived from Python source.

Python wrappers may use subprocesses, shells, native tools, multiprocessing,
and explicit worker coordination. Their use is not itself an unsafe condition.
The supervisor must keep every descendant within the same network, write, and
lifecycle boundary. A process that cannot be placed under that boundary makes
the execution ineligible.

### Worker Ownership

Every child and descendant must remain registered with its parent or the
reproduction supervisor until it exits. Detached processes are allowed only
when the supervisor can retain ownership and stop them. Successful execution
requires the complete worker tree to finish and all declared outputs to become
stable before comparison.

If the root command remains active after its accepted runtime limit, the
supervisor terminates the complete worker tree through the same bounded cleanup
path used by stop. It durably records a failed command result with reason
`execution_timeout` and a message naming the exceeded limit. The timeout is an
execution outcome: dependants are blocked by that prerequisite, independent
commands continue, and retained stdout and stderr remain queryable.

The preflight and runtime must reject unresolved absolute outputs, path escape,
unsafe symlink traversal, unsupported detached ownership, unavailable
confinement, and any other condition that prevents the required boundary.
Best-effort source inspection may produce precise early failures but does not
replace runtime controls.

Fixed worker, trace, path, output, temporary-storage, and grace-period bounds
are defined in [Fixed Resource Bounds](#fixed-resource-bounds).

## Artifact Comparison

### General Contract

Comparison is automatic, code-only, bounded, and versioned. No agent or
researcher decides equivalence during a run. Whole-artifact type-aware exact
comparison is the default. One generated file may explicitly select the
evidence-scoped exception defined below; no exception is inferred from format,
name, execution, or an observed difference.

Comparison applies to each artifact independently after its complete
execution output set is available. For selected producer-consumer edges, this
comparison completes before consumer readiness: only the exact matched inputs
permit that consumer to run. A mismatch or comparison inability remains an
artifact result and locally blocks consumers; it does not fail the producer or
abort unrelated work. Type-aware profiles compare decoded logical
content so incidental serialization differences do not create a change where
the approved profile defines them as irrelevant. A format without a recognized
decoder uses exact bytes when it is a regular file.

The closed v1 profiles are:

| Profile | Exact comparison |
| --- | --- |
| Opaque file | byte length and every byte |
| Text | decoded character sequence under the selected strict encoding |
| JSON | parsed object/array structure, key identity, scalar type, and scalar value |
| CSV/TSV table | ordered columns, ordered rows, cell type, null state, and value |
| Named scientific array container | member names, shapes, dtypes, structure, and every value |
| Image | decoded dimensions, mode/channel structure, frame structure, and every pixel value |
| Directory | bounded recursive normalized membership, member kind, and each member's selected profile |
| Compact completed-model bundle | directory profile with exactly `model.pt`, `metadata.json`, `training-history.csv`, and `artifact-manifest.json`; opaque bytes for the model, JSON for both JSON records, and table comparison for the history |

Dictionary key order and incidental JSON whitespace are not content. Table row
and column order are content. Array dtype and signed zero are content; NaN
equality follows the selected container profile and must be documented per
decoder. Image container metadata not included in decoded image structure is
not content. Directory paths are compared in normalized lexical order and no
member may escape through a symlink.

For CSV/TSV floating cells and NumPy, HDF5, or MATLAB floating array
components, NaNs compare equal only at the same logical positions; NaN payload
bits are not content. Positive and negative zero remain distinct. JSON rejects
non-finite numeric spellings rather than assigning them comparison semantics.

Text is strict UTF-8. CSV uses comma and TSV uses tab with the standard
double-quote escape rules. An empty table field is null; case-insensitive
`true` and `false` are booleans; canonical decimal integers without leading
zeroes are integers; decimal or exponent spellings and `nan`/`inf` spellings
are floating values; every other field is text. These rules apply to data
rows; header fields remain exact text column identities. Comparison does not
inspect authored schema hints.

An enclosing study directory uses the directory profile recursively; each
compact completed-model leaf uses the named dispatch above. Point-level
predictions are not implicit members of either artifact. Approximate numeric
comparison is not used for the Phase 7 format or its historical migration.

### Defensive Limits

Comparators must stream or memory-map where practical and must validate file
identity before and after reading. Fixed per-artifact bytes, directory members,
nesting, table cells, array members, decoded pixels, and working-memory limits
are defined in [Fixed Resource Bounds](#fixed-resource-bounds).

Exceeding a limit, encountering an unsupported representation, or failing a
decoder is never a match or change. A successfully regenerated artifact whose
comparison cannot complete has status `not-compared` and reason `comparison-failed` with a precise
reason such as `resource_limit`, `unsupported_format`, or `comparator_error`.
It and every available sibling output are retained for diagnosis.

### Evidence-Scoped Comparison

A generated file may opt into `research-log-evidence-scoped-comparison/1`
through its `data.json` item:

```json
"reproduction_comparison": {
  "contract": "research-log-evidence-scoped-comparison/1",
  "profile": "evidence"
}
```

The declaration is invalid on an origin, directory, or Git repository. It is
also invalid unless at least one non-artifact `evidence.json` record selects
the same canonical resource and every applicable record can be evaluated by
the existing locator and transformation contracts. Every applicable record
participates; reproduction may not choose a subset.

Whole-artifact comparison remains the first and cheapest step. When it
matches, the artifact is `matched` under its ordinary profile and evidence is
not extracted. The durable comparison nevertheless records the complete
evidence-definition identity. When the whole artifact differs, reproduction
evaluates every applicable record twice, substituting the regenerated path
only for the resource being reproduced. Locator structure and every selected
typed value compare exactly by default.

One non-artifact evidence record may declare an absolute tolerance:

```json
"reproduction_tolerance": {"absolute": "0.01"}
```

The value is a canonical positive finite decimal string. It applies to each
numeric selected item consumed by that record; selected nonnumeric items and
numeric kinds remain exact. A record with a tolerance must select at least one
numeric item. The tolerance defines equivalence only between retained and
regenerated evidence. It does not weaken the exact retained-value comparison
against Markdown.

Every applicable record must match. A completed comparison with a differing
selected value is `not-matched`. A missing value, invalid or incompatible
selection, failed transformation, or other inability to evaluate the declared
contract is `not-compared` with a comparison problem
`evidence_comparison_failed`. Regenerated output remains retained under the
ordinary run policy.

The definition identity covers the artifact declaration plus the complete
applicable evidence records, including sources, locators, transformations, and
tolerances. A relevant `data.json` or `evidence.json` change therefore makes a
prior comparison ineligible for reuse during fresh preparation. Evidence-scoped comparison is introduced only for
one reviewed legitimate nondeterministic artifact at a time after researcher
approval of its evidence set and smallest scientifically justified tolerance;
it is never populated across the corpus automatically.

## Staging And Promotion

### Staging

Reproduction retains every available regenerated output, including matched,
changed, partial, and comparison-failed outputs, in its original run-local
workspace path. It never deletes, discards, relocates, or makes a second copy of
an output after comparison. Captures and diagnostics likewise remain in the
run directory until a researcher deletes the directory manually.

The run directory is one of:

```text
<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>-<run-id>/
<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>-<entry>-<run-id>/
```

`YYYY-MM-DD` is the UTC calendar date of the run's immutable `accepted_at`
timestamp. A run that crosses midnight remains under its acceptance date. The
date organizes independent runs and does not identify a reproduction batch.
Each run is a direct child of its date directory; there are no intermediate log
or entry directories. `<log>` and `<entry>` are stable normalized
filesystem-safe identifiers.

Acceptance creates only the shared `reproduction/` root, the applicable date
directory, and the accepted run directory. A preview or read-only lookup
creates none of them. Existing-run lookup takes only the immutable run ID and
scans the immediate date directories for the exact matching leaf. Zero matches
is not found; more than one match is an integrity failure. There is no date
argument, persistent run index, or legacy-path lookup.

The directory contains run-local `state.sqlite`, one project-layout
`workspace/`, private runtime and diagnostic directories, and the retained
execution output trees. Native accepted work and durable command/artifact observations in
`state.sqlite` are the staging authority. Command results preserve the complete
declared output inventory; artifact results retain staged paths, comparison
profiles, fingerprints and applicable evidence. Comparisons commit before the
separate acknowledged `pyrun.json` requirement effect.

Reproduction must never overwrite or delete a retained run directory or its
staged output trees. There is no discard, cleanup, or supersede command. A
researcher may delete material directly from `<project>/tmp/reproduction`.

### Promotion

Promotion is explicit and separate from reproduction:

```text
log reproduce promote --path LOG --run-id RUN_ID --cid CID --execution-id EXECUTION_ID
```

The CID and execution ID select the complete indivisible output set recorded in
`pyrun.json`; individual artifact paths are not promotion selectors. Promotion
requires every output in the staged execution, verifies the accepted invocation
and frozen comparison evidence, recipe equality, output membership, staged
fingerprints, and destination-baseline
preconditions, then copies the complete set into maintained locations. A
partial or stale set cannot be promoted. The same atomic metadata update installs
the plan's accepted raw-script and effective-code observations with the complete
output fingerprints. The effective-code observation may be null and remains
noncurrent for later selection; source and outputs cannot be promoted independently.

Promotion copies; it never moves or modifies staged source files. Other
executions in the same run directory remain independently available. Missing
manually deleted staging material fails inspection or promotion clearly but
does not invalidate an already published reproduction result.

Promotion is a researcher-directed research mutation. It atomically updates
retained outputs and the related `pyrun.json`. Immutable saved outcomes and
latest-observation indexes remain unchanged; any report recovery renders only
the committed summary. It must not
change `data.json` declarations, evidence records or their artifact baselines,
or run validation. It leaves the retained run-local staged sources intact.

## Human And Agent Interfaces

There are no aliases or implicit launch route.

```text
log reproduce plan --path LOG [--entry ENTRY] [--include-all] [--recheck]
  [--jobs N] [--execution-timeout-seconds N] [--cursor CURSOR] [--format text|json]
log reproduce run --path LOG [--entry ENTRY] [--include-all] [--recheck]
  [--jobs N] [--execution-timeout-seconds N]
log reproduce show (--path LOG | --root PROJECT) [--run-id RUN] [--format text|json]
log reproduce list commands --path LOG [--run-id RUN] [--entry ENTRY]
  [--status STATUS] [--reason REASON] [--cursor CURSOR] [--format text|json]
log reproduce list artifacts --path LOG [--run-id RUN] [--entry ENTRY] [--cid CID]
  [--status STATUS] [--reason REASON] [--cursor CURSOR] [--format text|json]
log reproduce detail command --path LOG --entry ENTRY --cid CID --execution-id ID
  [--run-id RUN] [--section SECTION] [--cursor CURSOR] [--format text|json]
log reproduce detail artifact --path LOG --entry ENTRY --artifact ARTIFACT
  [--run-id RUN] [--section SECTION] [--cursor CURSOR] [--format text|json]
log reproduce render --path LOG
log reproduce status --path LOG --run-id RUN [--json]
log reproduce stop --path LOG --run-id RUN
log reproduce resume --path LOG --run-id RUN
log reproduce promote --path LOG --run-id RUN --cid CID --execution-id ID
```

`--run-id` is valid only for single-log saved inspection. Root show uses separate
compact command/artifact tables, exact totals, unavailable rows and entry-target
coverage qualifications. Single-log show and `reproduction.md` share the compact
hierarchy; reports contain no diagnostic inventories. Use list/detail to debug,
plan to inspect current work, and status to inspect an accepted job's lifecycle.
Run returns the newly accepted job, not a substituted older saved result.
No runnable work creates neither a job nor a saved run; the explicit empty
whole-log obsolete-history recovery exception uses a zero-confirmation receipt.

## Locking And Publication

Scope reservations use an exclusive reproduction-log lock for log targets,
or a shared log lock plus exclusive entry lock for entry targets. They remain
held through worker/permit/scratch cleanup. Overlapping targets are excluded;
distinct entry targets may run concurrently. Normal source/publication locks
are never held while commands execute.

The scheduler uses its existing project-wide SQLite coordinator, exclusive
ticket fairness and exact read/write/writable claims. Poll authenticates accepted
claims and live owner before attaching a grant through a short job-state mutation.
Grant-before-attachment and attachment-before-return interruptions are retryable.
Exact unchanged polls perform no writes. Wrong identities cannot release grants;
release is idempotent only for an already absent exact grant. Dead-owner grants
or waiters require quiescent native proof; live/surviving workers exclude reuse.

The detached start gate registers the actual child PID before execution.
Stop is durable intent, not a fabricated command failure. Fixed-plan resume
uses immutable acceptance and completed native facts; relaunchable stopped
attempts receive fresh scratch. Status is an observational snapshot and does not
inspect processes or recover an absent supervisor. Stop and resume may explicitly
recover an absent owner; ordinary failed runs cannot resume. Publication-only
resume opens no workspace and
performs no preflight, execution, planning or research-file reads.

Result publication owns the reproduction-publication lock then the shared
results lock. An immutable saved run and domain generation commit atomically.
Report writing/materialization acknowledgment follows; interruption is recovered
from frozen job observations and publication journal. Exact retries recognize
the original committed run rather than reapplying latest indexes. Requirement
clearing uses job then entry lock and acknowledges only after the atomic flag
write. Promotion retains its existing short reservation/install/rollback locks.

## Compatibility And Evolution

Reproduction is replacement-only: no migration, legacy readers, old-job resume
or CLI aliases. Explicit recheck installs/replaces only the reproduction domain;
validation and command-diagnostic domains remain intact. Reads and preview never
replace unsupported state. Jobs are not disposable cache and must be resolved
under their owning implementation before incompatible cutover. Retained research
and debugging files are not deleted or translated.

This does not remove the separately owned read-only legacy output-record
validation contract or change current pyrun recipe grammar.
Future contract/bound changes require explicit design, tests and documentation;
corpus growth does not dynamically adjust limits.

## Current Command-Verification Boundary

`log command verify` executes one isolated recorded invocation through the same
physical confinement/capture/comparison owners. It owns no reproduction job or
saved history, does not clear reproduction requirements, promote outputs or
replace validation snapshots. See the command-verification contract and usage
guidance for its independent selectors and retained debugging workspace.
