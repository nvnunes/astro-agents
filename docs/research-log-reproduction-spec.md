# Research-Log Reproduction Specification

## Status And Authority

Status: target implementation specification. The contract is not active until
the reproduction cutover is complete.

This document is the normative implementation contract for mechanical
research-log reproduction, the command-oriented `pyrun.json` record, durable
reproduction jobs, comparison, publication, and promotion. Code, tests,
generated records, public commands, and agent-facing projections must conform
to it.

The [mechanical reproduction concept](../tmp/research-log-pyrun-reproduction-concept.md)
owns the approved purpose and design rationale. The
[reproduction plan](../tmp/research-log-reproduction-plan.md) owns sequencing,
migration, verification, and completion. This specification owns the durable
runtime contract. It does not teach researchers how to use the workflow;
`docs/research-logging.md` and `skills/research-logging/` own that guidance.

The Phase 1 contract and the final Phase 2 resource bounds are complete. The
bounds are fixed versioned contract values recorded in
[Fixed Resource Bounds](#fixed-resource-bounds), not descriptions of the live
corpus. The compact completed-model format uses the existing exact directory
profile with opaque-byte, JSON, and table member dispatch. It requires no new
comparison family, non-exact equality, or change to execution identity,
authority, or graph traversal.

The key words **must**, **must not**, **should**, and **may** describe normative
requirements.

## Contract Map

- [Authority And Boundaries](#authority-and-boundaries) defines ownership and
  the relationship among Markdown, JSON state, validation, and reproduction.
- [`pyrun.json`](#pyrunjson) defines executable state, identity, observation,
  policy, publication, and lifecycle operations.
- [Migration](#migration) defines the metadata-only cutover and its independent
  remediation gate.
- [Discovery And Planning](#discovery-and-planning) defines targets, admission,
  graph traversal, slow boundaries, cycles, and dry runs.
- [Durable Reproduction Jobs](#durable-reproduction-jobs) defines launch,
  status, stop, resume, recovery, and exit semantics.
- [Execution Safety](#execution-safety) defines disposable execution, network
  denial, write confinement, and worker ownership.
- [Artifact Comparison](#artifact-comparison) defines exact type-aware
  comparison and defensive failure behavior.
- [Results And Currentness](#results-and-currentness) defines cumulative
  artifact outcomes and run history.
- [Staging And Promotion](#staging-and-promotion) defines retained changed or
  partial outputs and copy-based whole-execution promotion.
- [Locking And Publication](#locking-and-publication) defines scope protection,
  concurrent entry runs, and shared-state publication.
- [Human And Agent Interfaces](#human-and-agent-interfaces) defines
  `reproduction.md`, ready-to-present reports, and bounded machine queries.
- [Compatibility And Evolution](#compatibility-and-evolution) defines the
  cutover and extension boundaries.

## Versioned Surfaces

The initial implementation must use these versions:

| Surface | Version |
| --- | --- |
| Execution-state file | `research-log-pyrun/v1` |
| Execution identity | `pyrun-exec/v1:<sha256>` |
| Standard environment | `pyrun-standard/v1` |
| Execution contract | `research-log-pyrun-execution/1` |
| Reproduction result | `research-log-reproduction-result/1` |
| Durable run state | `research-log-reproduction-run/1` |
| Run status projection | `research-log-reproduction-status/1` |
| Dry-run plan | `research-log-reproduction-plan/1` |
| Source snapshot | `research-log-reproduction-source-snapshot/1` |
| Staging manifest | `research-log-reproduction-staging/1` |
| Comparison dispatch | `research-log-reproduction-comparison/1` |

Execution IDs version only their identity algorithm and canonicalization.
Schema, runner, standard-environment, execution-contract, and comparison
versions must not cause incidental execution-ID churn.

## Fixed Resource Bounds

These limits are code-owned v1 constants. They are not authored metadata or
CLI settings. A decoder rejects an over-limit durable file. Planning,
execution, comparison, and publication fail explicitly on an over-limit
operation; they never truncate, sample, or silently narrow it.

The initial limits were selected with measured retained-corpus headroom. They
remain unchanged as the corpus evolves; revisions follow the explicit process
in [Compatibility And Evolution](#compatibility-and-evolution).

### Execution State And Serialization

| Resource | Limit |
| --- | ---: |
| `pyrun.json` encoded bytes | 16 MiB |
| Executions per entry | 256 |
| Parameters per execution | 4,096 |
| Direct inputs per execution | 128 |
| Outputs per execution | 256 |
| Explicit environment variables per execution | 64 |
| Participating code paths per execution | 256 |
| Bytes per ordinary string | 8 KiB |
| Bytes per normalized path | 2 KiB |

### Planning And Graphs

| Resource | Limit |
| --- | ---: |
| Reachable executions per log target | 2,048 |
| Artifact cases per target | 10,000 |
| Total graph nodes | 16,384 |
| Total graph edges | 32,768 |
| Dependency depth | 64 |
| Boundaries per plan | 10,000 |
| Failures per plan | 10,000 |
| Dry-run plan encoded bytes | 64 MiB |

An entry target uses the same ceilings but cannot traverse a command outside
the selected entry. Graph limits do not authorize broader scope.

### Durable Runs, Workers, And Staging

| Resource | Limit |
| --- | ---: |
| `run.json` encoded bytes | 256 MiB |
| Status projection encoded bytes | 64 MiB |
| Staging manifest encoded bytes | 64 MiB |
| Registered workers per execution | 1,024 |
| Registered workers per run | 4,096 |
| Checkpoints per run | 2,048 |
| Outputs per checkpoint | 256 |
| Structured diagnostic events per run | 1,000,000 |
| Structured diagnostic bytes per run | 1 GiB |
| Runner-owned temporary and staging bytes per run | 1 TiB |
| Graceful stop interval | 30 seconds |
| Forced-stop verification interval | 10 seconds |

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

### Cumulative Results

| Resource | Limit |
| --- | ---: |
| `reproduction/results.json` encoded bytes | 64 MiB |
| Current artifact records | 10,000 |
| Retained or availability-unknown run records | 10,000 |

History pruning follows the filesystem-availability rules below. Reaching a
result limit is an explicit publication failure; it does not authorize
discarding available run history or current artifact state.

## Terminology

- **Execution recipe:** the normalized structural information required to
  invoke one child process and associate its direct inputs and complete output
  set.
- **Execution ID:** the stable `pyrun-exec/v1:<digest>` identity of one recipe.
- **Reproduction run:** one durable entry- or log-target reproduction job.
- **Run ID:** the opaque, filesystem-safe identity of one reproduction run. It
  is distinct from every execution ID and is preserved by resume.
- **Observed execution state:** retained fingerprints for the directly executed
  script, participating local Python code, direct inputs, and complete outputs.
- **Confirmation:** whether one complete recipe and its observations were
  established by an eligible successful execution.
- **Artifact case:** one evidence-relevant retained generated file or directory
  evaluated independently.
- **Evidence root:** a retained source artifact selected by an `evidence.json`
  record in the requested target.
- **Retained boundary:** a fingerprint-verified input whose producer is outside
  the permitted execution scope or is skipped by the default slow policy.
- **Scope lock:** the one existing research-log operation lock held for the
  selected entry or log throughout an active run.
- **Publication mutex:** the brief log-local lock used to serialize shared
  result, report, validation-refresh, and active-operation-state writes.

## Authority And Boundaries

### Reproduction Authority

The operational authority is:

| Surface | Authority |
| --- | --- |
| Research-log Markdown | Human research account, evidence presentation, and explanatory command history |
| `evidence.json` | Reproduction roots and exact retained evidence-source identity |
| `data.json` | Named material location, fingerprint, and origin/generated classification |
| `pyrun.json` | Current executable recipes and observed execution state |
| `validation/results.json` | Reproduction admission gate |
| `validation.md` | Disposable human validation projection only |
| `reproduction/results.json` | Cumulative authoritative reproduction results and published run index |
| Durable run directory | Active, stopped, failed, and staged run-specific operational state |
| `reproduction.md` | Disposable human reproduction projection only |

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

Reproduction may change only its generated state, a confirmation-only field in
`pyrun.json`, and the surgically affected validation state defined below. It
must not edit research prose, Markdown commands, evidence presentation,
`data.json` declarations, retained artifacts, or other human-authored log
content. Promotion is the separate researcher-directed exception for replacing
retained artifacts.

## `pyrun.json`

### Ownership And Location

Each entry root may contain one generated `pyrun.json`. `pyrun` and its
explicit lifecycle services own it. Researchers and agents must not edit it
directly. An entry with no current execution state omits the file.

The file records current executable state, not attempts or history. It has no
output index, comparison policy, reproduction timestamps, failed attempts, or
superseded recipes.

### File Shape

The file is strict UTF-8 JSON with no duplicate keys, non-finite numbers,
unknown fields, or trailing content, and with one trailing newline. It has
exactly:

```json
{
  "schema": "research-log-pyrun/v1",
  "executions": {
    "pyrun-exec/v1:0123456789abcdef...": {
      "confirmed": true,
      "slow": false,
      "last_run_at": "2030-01-01T00:00:00Z",
      "runner": "research-log-pyrun-runner/1",
      "environment_profile": "pyrun-standard/v1",
      "execution_contract": "research-log-pyrun-execution/1",
      "recipe": {
        "script": "scripts/run_study.py",
        "parameters": [
          "--input-data",
          "<catalog>",
          "--output-csv",
          "data/results.csv"
        ],
        "environment": {},
        "inputs": ["catalog"],
        "outputs": {
          "data/results.csv": "file",
          "images/results.png": "file"
        }
      },
      "observed": {
        "script": {
          "algorithm": "sha256",
          "digest": "..."
        },
        "inputs": {
          "catalog": {
            "algorithm": "sha256",
            "digest": "..."
          }
        },
        "code": {
          "scripts/helpers.py": {
            "algorithm": "sha256",
            "digest": "..."
          }
        },
        "outputs": {
          "data/results.csv": {
            "algorithm": "sha256",
            "digest": "..."
          },
          "images/results.png": {
            "algorithm": "sha256",
            "digest": "..."
          }
        }
      }
    }
  }
}
```

Top-level keys are exactly `schema` and `executions`. Execution-map keys are
unique execution IDs. Every execution value has exactly `confirmed`, `slow`,
`last_run_at`, `runner`, `environment_profile`, `execution_contract`, `recipe`,
and `observed`.

`confirmed` and `slow` are required Booleans. `last_run_at` is either `null` or
a UTC RFC 3339 timestamp with whole seconds and `Z`. A metadata-rebuilt
migration record uses `null` because no ordinary `pyrun` completion time is
known. Version fields are required nonempty identifiers from the code-owned
supported sets.

`recipe` has exactly `script`, `parameters`, `environment`, `inputs`, and
`outputs`:

- `script` is the normalized POSIX script argument. A script beneath the entry
  uses its entry-relative identity; any other script beneath the maintained log
  uses the inherited `<log>/...` identity; and a script elsewhere in the
  current Git project uses `<project>/...`. Scripts outside the project are not
  eligible.
- `parameters` is the exact ordered child-process argument tail after the
  script. It contains no runner role declarations, capture options, explicit
  environment options, or separator token.
- `environment` maps each explicit normalized `--env NAME=value` variable name
  to its exact value. It contains no inherited or runner-supplied variable.
- `inputs` is the sorted unique list of directly consumed `data.json` names.
- `outputs` maps every declared output identity to `file` or `directory`.
  Entry-owned outputs use their normalized entry-relative path. Outputs
  elsewhere in the current Git project use the inherited normalized
  `<project>/...` identity.

`observed` has exactly `script`, `inputs`, `code`, and `outputs`:

- `script` is the fingerprint of the directly executed script.
- `inputs` maps every recipe input name to its execution-time fingerprint.
- `code` maps every eligible participating local Python source other than the
  directly executed script to its execution-time fingerprint, using the final
  local-code-dependency path and observation rules owned by the mechanical
  validator specification.
- `outputs` maps every recipe output identity to its execution-time
  fingerprint.

A commit-pinned `git-repository` input remains a `data.json` origin. Its recipe
input is still the data name, and its observed value uses the inherited exact
repository-and-commit fingerprint form. Reproduction resolves and verifies the
recorded commit; it must not substitute the current checkout or a branch tip.

The recipe and observed input key sets must agree exactly. The recipe and
observed output key sets must agree exactly. Every fingerprint uses the closed
fingerprint forms owned by `data.json` and the mechanical validator
specification. `data.json` remains the sole owner of input paths,
classifications, and expected input fingerprints.

The fixed file, execution, parameter, string, input, output, environment, and
code limits are defined in [Fixed Resource Bounds](#fixed-resource-bounds).
Exceeding a bound is invalid state; readers must not truncate it.

### Execution Identity

An execution ID has the form `pyrun-exec/v1:<digest>`, where `<digest>` is the
lowercase hexadecimal SHA-256 digest of the canonical identity projection.

The identity projection contains exactly:

```json
{
  "environment": {},
  "inputs": [],
  "outputs": {},
  "parameters": [],
  "script": "scripts/run_study.py"
}
```

It uses canonical UTF-8 JSON with lexicographically sorted object keys, compact
separators, no ASCII escaping, and no trailing newline. Array order is retained
for `parameters`; `inputs` is sorted before serialization; environment and
output map keys are sorted by canonical JSON serialization.

The projection includes the normalized script, ordered child parameters,
explicit environment variables, direct input names, and complete output paths
and kinds. It excludes observations, confirmation, slow policy, timestamps,
Markdown location, standard-environment profile, schema version, runner
version, and execution-contract version.

Changing script bytes or direct-input bytes makes observed state stale without
changing the execution ID. Changing the script path, parameters, explicit
environment, direct input names, or output membership creates a different ID.
The same normalized recipe, including each concrete expansion of a static
loop, always reuses its ID. A Markdown command or loop has no separate shared
execution ID.

### Eligible Invocation

Only `pyrun` may establish reproduction-eligible execution state. One authored
command block may contain one or more `pyrun` invocations and bounded static
shell loops whose concrete expansions are independent invocations.

Production command blocks must not use direct non-`pyrun` executables,
pipelines, redirection, `tee`, shell environment prefixes, command or process
substitution, dynamic shell discovery, or other general shell interpretation.
Equivalent behavior must use a `pyrun` facility or a retained Python wrapper.
A retained Python wrapper may invoke any required external executable.

Runner-visible input and output roles are ingestion metadata. Once `recipe`
and `observed` contain explicit associations, reproduction neither stores nor
replays those role declarations.

### Standard Environment

Ordinary `pyrun` and reproduction use the current project's
`.conda/bin/python`. The interpreter fingerprint, installed-package inventory,
and complete inherited process environment are outside the contract.

The versioned standard environment supplies runner-controlled temporary
`MPLCONFIGDIR` and `XDG_CACHE_HOME` locations. Qualified external runtimes
receive equivalent runner-owned preference locations where required. Concrete
temporary paths are not serialized.

Repeatable `--env NAME=value` options are normalized into `recipe.environment`
and participate in identity. A missing project environment or required
executable prevents execution. Environment drift that still executes and
changes output is reported through artifact comparison rather than diagnosed
by inference.

### Atomic Publication And Replacement

One execution owns one complete unconditional output set. Output paths within
an execution must not duplicate, alias, overlap as file and directory, or have
an ancestor-descendant relationship. Each current output has exactly one
current execution owner.

Ordinary `pyrun` publishes only after:

1. the child exits successfully;
2. the script, direct inputs, and observed local Python code remain stable;
3. every declared output exists with the declared kind and can be observed
   completely; and
4. candidate state passes the complete production decoder and ownership
   checks.

A successful identical recipe atomically replaces its observed state. A
successful new recipe whose output set overlaps existing recipes atomically
removes every overlapping recipe in full and installs the new execution.
Failed or incomplete execution, capture, observation, or publication changes
no `pyrun.json` state.

`last_run_at` records the completion time of the latest successful atomic
ordinary `pyrun` publication. It is `null` for a metadata-rebuilt migration
record until a later ordinary publication establishes such a time. Failed
attempts, reproduction execution, and confirmation-only or slow-only mutations
must not change it. Ordinary `pyrun` reads and writes only its entry-local
state; it must not load, scan, mark, or rewrite log-wide reproduction results.

An ordinary successful publication is confirmed. A complete unconfirmed
recipe remains runnable. Reproduction changes `confirmed` to true only when
every output in that execution has the `matched` outcome in one completed run.
That confirmation-only mutation preserves the recipe, observations, policy,
versions, and `last_run_at`. Any changed, failed, comparison-failed, or skipped
output leaves it unconfirmed.

### Slow Policy

`pyrun --slow -- script.py ...` records `slow: true`. Omitting `--slow`
records `slow: false`. Slow means that the simulation, model training, or
similar execution must not be rerun casually. It is an authored policy, not a
measured duration.

Slow is outside identity. A later classification-only change uses exactly one
of:

```text
log pyrun update --path LOG --entry ENTRY --execution-id ID --slow
log pyrun update --path LOG --entry ENTRY --execution-id ID --no-slow
```

The operation requires exactly one policy flag, takes the selected entry lock,
changes only `slow`, and writes atomically. The researcher or authoring agent
must first edit only the `--slow` token in Markdown. The operation resolves the
supplied execution ID to one concrete expanded recipe and its containing
authored invocation, requires exact structural agreement apart from the
requested policy difference, and never edits Markdown.

If the authored invocation is one bounded static loop, the operation resolves
the complete expansion and atomically applies the same policy to every
distinct affected execution ID. It never merges those executions. Any other
Markdown-to-state disagreement is a refusal. The operation neither runs the
recipe nor refreshes validation; ordinary validation remains the separate next
authoring step.

### Retirement

Retirement removes one complete execution and all of its output support through
the owning lifecycle service. It is permitted only after proving that no
maintained evidence or downstream generated-data dependency requires any
output without another valid producer. Retirement must be explicit and
researcher-approved; no migration or cleanup path may infer it.

## Migration

### Boundary

Migration is one metadata-only conversion from the final post-authoring
`pyrun-outputs.json` state to `pyrun.json`. It must not invoke `pyrun`, a
research script, a wrapper, or any other research executable. There is no
compatibility period and no knowingly unmigrated case. After cutover, no
runtime, validation, reorganization, or reproduction path reads
`pyrun-outputs.json`.

Markdown may be used once during migration as evidence of the current
mechanically valid `pyrun` recipe and its fixed output declaration. It is not
authority after cutover.

### Reconstruction Proof

Each current validated Markdown command may become one execution only when all
of the following hold:

- the command yields exactly one normalized recipe after bounded static-loop
  expansion;
- its complete output declaration is unconditional;
- the current script, direct inputs, and inherited local Python code-dependency
  state are complete under the final authoring contract;
- every declared output exists with the expected kind and can be observed
  completely;
- no output ownership conflict exists; and
- the complete candidate passes the production `pyrun.json` decoder and
  ownership checks.

Migration reuses an agreeing legacy observation when available and directly
observes any missing current artifact. Legacy directory-member fingerprints do
not prove a directory-root observation; migration observes the existing root
with the normal directory algorithm. This records current retained state, not
successful execution history.

The migrated record is confirmed only when matching legacy records exactly
cover the current output set in the same representation and every one states
that it is confirmed. Otherwise the rebuilt execution is unconfirmed and uses
`last_run_at: null`. Migration must never promote confirmation or invent an
ordinary run timestamp. An unmatched legacy signature is not preserved as an
orphaned recipe, and conditional or optional output membership is not converted
into an atomic execution.

### Pre-Migration Remediation

Before migration, an audit must produce one deterministic human-facing
Markdown remediation log. It is a review record, not executable input or
migration authority. Every case includes:

- stable case identity and source location;
- one reason code;
- concise human detail;
- researcher-approved disposition;
- affected evidence and downstream generated-data consumers; and
- verification evidence for the applied resolution.

The closed initial reason-code taxonomy is:

- `missing_output_observation`;
- `directory_representation_mismatch`;
- `missing_current_command`;
- `conditional_output_set`;
- `ambiguous_mapping`; and
- `missing_material`.

A new blocker class requires an explicit contract amendment, not a catch-all
code. Allowed resolutions are metadata rebuild from a current validated
Markdown command and existing retained artifacts, command correction,
output-set normalization, separation into fixed-output executions, restoration
of an unambiguous current mapping, material restoration, or explicit
retirement.

Migration rebuilds a complete execution from the current validated Markdown
command, `data.json`, retained local-code dependency state, and the existing
declared artifacts. It reuses agreeing legacy observations where available and
observes missing current outputs directly without executing the research
command. A legacy member-by-member directory representation is never folded
into a directory-root digest; migration observes the existing directory root
with the normal directory fingerprint algorithm. A rebuilt execution remains
unconfirmed unless complete agreeing legacy evidence proves confirmation.

A missing declared artifact, wrong artifact kind, conditional output set,
unresolvable input, missing script or participating code path, or ambiguous
current command remains a genuine blocker. Metadata reconstruction never
claims that an artifact was regenerated or that an ordinary `pyrun`
publication occurred.

Migration must independently rescan actual corpus state and require zero
genuine blockers. It must not trust the remediation log as authority. Any new
or unresolved blocker aborts without partial cutover or omission.

## Discovery And Planning

### Public Target

The public launch form is:

```text
log reproduce --path LOG [--entry ENTRY] [--include-slow] [--dry-run]
```

Omitting `--entry` selects exactly one complete log. Supplying `--entry`
selects exactly that stable entry. There is no multi-log, all-log, or
project-wide reproduction operation.

Evidence records inside the selected target define initial artifact cases.
Only artifacts reachable from those current evidence roots participate in
current coverage or execution planning.

### Admission Gate

Before accepting or previewing work, reproduction requires a current completed
`validation/results.json` for the exact source snapshot. It rejects:

- any Structure failure;
- any Evidence failure;
- any failed Provenance artifact;
- incomplete, malformed, unsupported, or stale validation state; and
- any active operation or source condition that prevents a stable plan.

Unconfirmed Provenance and Hygiene findings do not block reproduction.
Unconfirmed runnable recipes are deliberately eligible so reproduction can
establish confirmation.

The validation subsystem's artifact-level Provenance projection determines
whether a failed Provenance artifact exists. Reproduction must not classify
raw check failures independently. In particular, a
`summary.reference.target_invalid` check whose exact target is
`provenance.output.unconfirmed` belongs to the same admissible unconfirmed
state and does not create an additional admission blocker. A summary-target
failure with any other cause remains subject to the normal Structure,
Evidence, or failed-Provenance-artifact gate.

### Graph Construction

Reproduction constructs a bounded graph only from current `evidence.json`,
`data.json`, and `pyrun.json`:

1. resolve every target evidence source to its declared data item;
2. stop at `origin: true` inputs;
3. for each generated artifact, find exactly one owning execution by canonical
   output identity;
4. resolve every direct execution input through its owning `data.json`; and
5. repeat until every branch reaches an origin or retained boundary.

The in-memory output-to-execution index is derived from the loaded execution
maps and is not persisted. Input names do not establish cross-entry artifact
identity; canonical resolved artifact targets do.

Entry-level reproduction must not execute a command owned by another entry. A
generated cross-entry dependency becomes a fingerprint-verified retained
boundary. Log-level reproduction must not execute a command outside the log.
A source entering a log from outside it must be a declared origin; a cross-log
generated input is invalid provenance.

Every selected execution includes the complete inherited local Python code
dependency projection. Missing or changed participating code affects
admission, planning, currentness, and guarded resume exactly as the final
authoring contract requires.

### Slow Boundary

By default, planning stops before every required `slow: true` execution. Its
retained output may serve as a boundary only when its current fingerprint and
required provenance state are valid. This boundary is planning metadata, not
an artifact outcome.

`--include-slow` includes slow executions within the same selected entry or log
boundary and traverses their upstream closure. Scope is immutable after run
acceptance. The CLI must not prompt to widen it.

### Failures And Ordering

Missing or multiple producers, invalid boundaries, resource-limit violations,
and cycles are mechanical artifact failures. A reachable dependency cycle
fails every affected component member with reason `dependency_cycle`; no
execution in the cycle runs. Independent acyclic components may continue.
Artifacts not attempted after a required upstream failure are `skipped` with
reason `dependency_failed`.

The planner groups cases by execution ID, schedules each execution once in a
deterministic dependency order, and preserves artifact-level result identity.
It selects all and only new, failed, stale, and dependency-affected current
cases required by the target. It must not infer a reduced plan from prior
matches when a current dependency invalidates them.

Graph node, edge, depth, execution, and projection limits are fixed and
code-owned in [Fixed Resource Bounds](#fixed-resource-bounds). Exceeding a
limit fails the affected planning operation; it never silently narrows the
graph.

### Dry Run

`--dry-run` applies the same admission, discovery, graph construction, slow
policy, selection, and safety preflight as a real launch. It emits one
deterministic `research-log-reproduction-plan/1` projection with exactly
`schema`, `summary`, `target`, `include_slow`, `validation_snapshot`,
`source_snapshot`, `cases`, `executions`, `boundaries`, and `failures`.

`target` follows the target grammar below. Cases are sorted by canonical log
entry order and artifact path. Each case has exactly `entry`, `artifact`,
`execution_id`, `disposition`, and `reason`; `disposition` is `run`, `current`,
or `failed`, and `reason` is null only when no qualification is needed.

Executions are in deterministic run order and each has exactly `order`,
`entry`, `execution_id`, `depends_on`, `outputs`, and `slow`. `depends_on` and
`outputs` are sorted unique identity arrays. Boundaries are sorted and each has
exactly `kind`, `entry`, `name`, `artifact`, and `fingerprint`; `kind` is
`origin`, `cross_entry`, or `slow`. Fields inapplicable to a boundary kind are
null rather than omitted. Failures are sorted artifact projections with exactly
`entry`, `artifact`, `outcome`, `reason`, and `dependencies`.

The validation snapshot has exactly `result_path`, `result_date`,
`rules_version`, `result_digest`, and `source_projection_digest`. The last two
are SHA-256 digests of the exact completed result and validation-owned complete
research-source projection admitted for reproduction. The mechanical validator
owns that projection's construction; reproduction treats it as an opaque
currentness token.

The source snapshot uses
`research-log-reproduction-source-snapshot/1` and has exactly `schema`,
`authority_files`, `executions`, and `materials`. `authority_files` records the
canonical path and SHA-256 bytes of every `evidence.json`, `data.json`, and
`pyrun.json` loaded for the plan. `executions` records each selected execution
ID and the SHA-256 digest of its canonical execution record. `materials`
records every current script, participating code file, direct input, retained
boundary, and comparison baseline by canonical identity, role, kind, and
closed fingerprint. All arrays are unique and canonically sorted. This snapshot
is the exact acceptance, final-publication, and resume comparison boundary.

Dry run is completely write-free. It creates no run ID, lock, working copy,
staging directory, checkpoint, result, report, cache, or other state. Because
it deliberately takes no scope lock, it records and rechecks the complete
source snapshot immediately before returning. A changed snapshot is an
operational failure, not a stale preview.

## Durable Reproduction Jobs

### Launch And Identity

Every non-dry launch creates one durable background job, persists its accepted
scope and source snapshot, starts its supervisor, emits its run ID, and returns
immediately. The job is independent of the invoking terminal and agent turn.
There is no foreground mode.

A run ID is an opaque, lowercase, filesystem-safe unique token produced by the
CLI. It is immutable and names the durable state, disposable copy, diagnostics,
and staging paths for the life of the run. It is not derived from Markdown or
an execution recipe.

The accepted target, entry-or-log kind, and slow-inclusion policy are immutable.
Management commands use only the recorded scope:

```text
log reproduce status --path LOG --run-id RUN_ID [--json]
log reproduce stop --path LOG --run-id RUN_ID
log reproduce resume --path LOG --run-id RUN_ID
```

They must reject `--entry` and `--include-slow`.

### Durable State

Each run directory contains one canonical `run.json` using
`research-log-reproduction-run/1`. Its top-level object has exactly:

```json
{
  "schema": "research-log-reproduction-run/1",
  "run_id": "reproduce-...",
  "summary": "docs/research.md",
  "target": {"kind": "entry", "entry": "e003"},
  "include_slow": false,
  "source_snapshot": {},
  "validation_snapshot": {},
  "plan": {},
  "state": {
    "status": null,
    "phase": "executing",
    "current_execution": "pyrun-exec/v1:...",
    "latest_failure": null
  },
  "progress": {
    "completed_executions": 2,
    "total_executions": 5,
    "artifact_outcomes": {
      "matched": 2,
      "changed": 0,
      "failed": 0,
      "comparison_failed": 0,
      "skipped": 0
    }
  },
  "timestamps": {
    "accepted_at": "2030-01-01T00:00:00Z",
    "started_at": "2030-01-01T00:00:01Z",
    "updated_at": "2030-01-01T00:00:02Z",
    "stopped_at": null,
    "resumed_at": null,
    "finished_at": null
  },
  "paths": {
    "run": "tmp/reproduce-research-e003-reproduce-...",
    "working_copy": "worktree",
    "diagnostics": "diagnostics",
    "staging": "executions"
  },
  "workers": [],
  "checkpoints": []
}
```

`target` has exactly `kind` and `entry`. `kind` is `entry` or `log`; `entry`
is the stable entry ID for an entry target and null for a log target.
`source_snapshot` and `validation_snapshot` are byte-for-byte the projections
defined by dry-run planning. `plan` is the accepted
`research-log-reproduction-plan/1` object without its outer `schema` and must
not change after acceptance.

`state.status` is null while active and otherwise one terminal status:
`complete`, `stopped`, or `failed`. `state.phase` is one of `accepted`,
`planning`, `preflight`, `executing`, `comparing`, `publishing`, `stopping`, or
null; it is null in terminal state. `current_execution` is an execution ID only
while one execution is active and otherwise null. `latest_failure` is null or
one object with exactly `code`, `message`, `execution_id`, and `recorded_at`;
`execution_id` may be null for a run-level failure.

`progress` has exactly the fields shown. Every outcome count is a nonnegative
integer. `timestamps` has exactly the fields shown; absent lifecycle events are
null. Paths are normalized run-directory-relative paths except `run`, which is
project-relative. Worker and checkpoint arrays are sorted by their stable
identities.

Each worker item has exactly `worker_id`, `parent_worker_id`, `pid`,
`execution_id`, `state`, `registered_at`, and `last_observed_at`.
`parent_worker_id` and `execution_id` may be null where their relationship is
not applicable. Each checkpoint item has exactly `execution_id`, `state`,
`path`, `completed_at`, and `outputs`; `state` is `active`, `complete`, or
`partial`, and fields unavailable in that state are null. Output entries use
canonical output identities and observed fingerprints.

The run record therefore durably retains:

- run ID, log, target kind, target entry when applicable, and include-slow
  policy;
- accepted source and validation snapshots;
- immutable deterministic execution plan;
- run status, current phase, current execution, and latest failure;
- accepted, started, updated, stopped, resumed, and finished timestamps where
  applicable;
- completed and total execution counts;
- accumulated artifact-outcome counts;
- per-execution checkpoints and worker registrations;
- disposable-copy, diagnostics, and staging paths; and
- stop, interruption, recovery, and publication state required for idempotent
  continuation.

Unknown fields fail. Checkpoint writes must be atomic and sufficient to
distinguish completed work from an active or partial execution after process or
host failure. Cardinality and byte limits are defined in
[Fixed Resource Bounds](#fixed-resource-bounds) and do not weaken this state
contract.

### Status

Default status is concise human text. `--json` emits one deterministic
`research-log-reproduction-status/1` object containing exactly `schema`,
`run_id`, `summary`, `target`, `include_slow`, `status`, `phase`,
`current_execution`, `completed_executions`, `total_executions`,
`artifact_outcomes`, `timestamps`, `latest_failure`, and `surviving_workers`.
The values are the corresponding strict projection of `run.json`.
`surviving_workers` is normally empty and, while stopping cleanup remains
incomplete, contains the exact sorted worker records still observed alive.

Agents and scheduled monitors must consume JSON rather than parse human text.

### Run And Artifact States

Terminal run statuses are:

- `complete`: the job reached its normal endpoint and final publication
  succeeded;
- `stopped`: execution is not active, the run retains resumable same-path
  state, and the scope lock has been released; and
- `failed`: an operational failure prevented final artifact-result
  publication.

`stopping` is an active phase, not a terminal status. Artifact changes or
failures do not make a successfully published run operationally failed. A
complete run may contain any artifact outcome.

### Stop

`stop` is the sole user operation for ending active work without deleting it.
It signals the complete supervised worker tree for graceful shutdown, waits one
fixed code-owned grace period, then force-terminates every survivor. It does
not wait for the current execution to finish naturally.

The run becomes `stopped` and releases its scope lock only after no supervised
worker remains. It retains the same run ID, disposable path, checkpoints,
partial outputs, and diagnostics.

If forced termination leaves a survivor, the run remains active in `stopping`,
retains its lock, records exact survivor diagnostics, and the stop request
returns nonzero. Repeating `stop` retries the bounded cleanup.

### Resume

`resume` is available only for `stopped` runs. It reacquires the original scope
lock, reuses the same disposable project copy and run paths, skips completed
execution checkpoints, and reinvokes the stopped execution in place. This
preserves script-native checkpoint and resume behavior.

Before executing, resume must verify exact agreement with the recorded recipes,
scripts, participating code, inputs, retained comparison artifacts, and
mechanically validated source snapshot. Any difference refuses resume without
deleting the old run; a new reproduction run is required.

### Recovery

Host or supervisor recovery performs reconciliation and worker cleanup only. It
must never restart research execution automatically. Every formerly active run
is reconciled, surviving registered workers receive the same bounded cleanup,
and the run becomes reason-coded `stopped` only after no worker remains. The
scope lock is not released earlier. Execution continues only after explicit
`resume` passes ordinary guards.

### Exit Status

Process exit status reports whether the requested CLI operation succeeded, not
the eventual run or artifact outcome:

- launch returns zero after durable acceptance;
- status returns zero after retrieving the requested run regardless of its
  state;
- dry run returns zero only for a valid stable plan;
- stop, resume, and promotion return zero only when the requested operation
  succeeds; and
- invalid input, refusal, conflict, or operational failure returns nonzero.

Artifact and run outcomes remain in durable status and results. They are never
encoded in launch or status exit status.

## Execution Safety

### Disposable Execution

Every job executes in a disposable project copy bound to its run ID. The copy
must preserve the project-relative, log-relative, and entry-relative layout
expected by recipes without writing generated artifacts into the maintained
project.

The executor makes regenerated upstream outputs available to downstream
recipes within that copy. It uses the project-local Python environment and the
recorded execution environment. Runner-owned temporary and cache locations,
including `MPLCONFIGDIR` and `XDG_CACHE_HOME`, are located inside the run's
allowed paths.

Retained inputs and comparison baselines are read-only. Generated outputs,
temporary files, checkpoints, captures, and diagnostics are confined to the
disposable project copy and runner-owned project-local temporary paths. A
recipe containing an output or effective write target outside those locations
must fail preflight.

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

The preflight and runtime must reject unresolved absolute outputs, path escape,
unsafe symlink traversal, unsupported detached ownership, unavailable
confinement, and any other condition that prevents the required boundary.
Best-effort source inspection may produce precise early failures but does not
replace runtime controls.

Fixed worker, trace, path, output, temporary-storage, and grace-period bounds
are defined in [Fixed Resource Bounds](#fixed-resource-bounds).

## Artifact Comparison

### General Contract

Comparison is automatic, exact, code-only, bounded, and versioned. No agent or
researcher decides equivalence during a run. Selection uses artifact kind and
recognized format; there is no authored override in v1.

Comparison applies to each artifact case independently after its complete
execution output set is available. Type-aware profiles compare decoded logical
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
comparison cannot complete has outcome `comparison_failed` with a precise
reason such as `resource_limit`, `unsupported_format`, or `comparator_error`.
It and every available sibling output are retained for diagnosis.

### Future Non-Exact Comparison

Non-exact comparison is deferred. If retained evidence later demonstrates a
need, an approved named and versioned exception belongs to the affected
artifact in `data.json`. It must not be command-level state in `pyrun.json`, an
agent judgment, or an implicit tolerance.

## Results And Currentness

### Artifact Outcomes

The artifact outcomes are:

- `matched`: the artifact was regenerated and compared equal;
- `changed`: the artifact was regenerated and compared unequal;
- `failed`: the artifact was not regenerated because its own production or
  graph condition failed;
- `comparison_failed`: the artifact was regenerated but comparison could not
  complete; and
- `skipped`: the artifact was not attempted because a required prior condition
  prevented it.

Outcome and currentness are separate. Reason codes are a closed versioned
machine vocabulary. At minimum, cycles use `dependency_cycle` with `failed`,
and downstream blocking uses `dependency_failed` with `skipped`. A default
slow boundary or a permitted cross-entry retained boundary is graph metadata,
not a skipped artifact outcome.

### Authoritative Result

`<log>/reproduction/results.json` is strict canonical UTF-8 JSON using
`research-log-reproduction-result/1`. It has exactly this shape:

```json
{
  "schema": "research-log-reproduction-result/1",
  "summary": "docs/research.md",
  "updated_at": "2030-01-01T00:05:00Z",
  "artifacts": [
    {
      "entry": "e003",
      "artifact": "data/result.csv",
      "execution_id": "pyrun-exec/v1:...",
      "outcome": "matched",
      "reason": null,
      "recorded_at": "2030-01-01T00:05:00Z",
      "run_id": "reproduce-...",
      "comparison": {
        "contract": "research-log-reproduction-comparison/1",
        "profile": "table",
        "expected": {"algorithm": "sha256", "digest": "..."},
        "regenerated": {"algorithm": "sha256", "digest": "..."}
      }
    }
  ],
  "runs": [
    {
      "run_id": "reproduce-...",
      "target": {"kind": "entry", "entry": "e003"},
      "include_slow": false,
      "status": "complete",
      "accepted_at": "2030-01-01T00:00:00Z",
      "finished_at": "2030-01-01T00:05:00Z",
      "artifact_outcomes": {
        "matched": 1,
        "changed": 0,
        "failed": 0,
        "comparison_failed": 0,
        "skipped": 0
      },
      "folder": {
        "path": "tmp/reproduce-research-e003-reproduce-...",
        "availability": "available"
      }
    }
  ]
}
```

`summary` is the maintained summary path. `updated_at` is the latest successful
artifact-result or run-index publication time. `artifacts` is sorted by
canonical log entry order, then artifact path. The pair `(entry, artifact)` is
unique. `runs` is sorted by descending accepted time, then run ID, and has one
record per retained or availability-unknown run.

Every artifact record has exactly `entry`, `artifact`, `execution_id`,
`outcome`, `reason`, `recorded_at`, `run_id`, and `comparison`. `reason` is null
for `matched`; it is a required code for every other outcome. `comparison` is
null when comparison was not attempted. Otherwise it has exactly `contract`,
`profile`, `expected`, and `regenerated`. `expected` and `regenerated` are the
closed observed fingerprint forms; a comparison failure that could not observe
one side uses null for that side. Detailed differences and decoder diagnostics
remain in the run directory rather than expanding this cumulative record.

Every run item has exactly the fields shown. Its target follows the run-state
target grammar. `status` is `complete`, `stopped`, or `failed`; an active run is
read through status and is added to the published index only when a lifecycle
event safely publishes it. `finished_at` is null for a resumable stopped run.
Outcome counts use all five required keys. `folder.path` is the normalized
project-relative run directory; `availability` is `available` or `unknown`.
A conclusively absent directory causes the whole run item to be removed rather
than persisting an `absent` value.

Unknown fields, duplicate artifact pairs, duplicate run IDs, invalid ordering,
or inconsistent counts fail decoding. The cardinality and byte limits in
[Fixed Resource Bounds](#fixed-resource-bounds) do not change this field
grammar.

### Cumulative Publication

A run that reaches its normal mechanical endpoint publishes every selected
artifact outcome, including non-success outcomes. Final artifact publication
depends on completion of the requested mechanical operation, not universal
matching.

Entry-level publication replaces only selected current cases and actually
regenerated supporting outputs for that entry. It preserves unrelated entry
and log cases and never claims log-level completion. Log-level publication
reconciles the complete selected log closure.

A stopped run or an operational failure before final publication leaves the
current artifact map unchanged. Terminal lifecycle events may still update the
run index and human Runs table without publishing partial artifact outcomes.

### Currentness

Every artifact result records `recorded_at`, the commit time of that result to
`reproduction/results.json`, regardless of outcome. A result is implicitly
stale when the producing execution has a non-null `last_run_at` later than
`recorded_at`. Recipe, script, code, input, validation, and dependency changes
may also make a case ineligible or require new work under the graph contract.

Currentness is derived when planning, querying, or rendering. Ordinary
`pyrun` never reads reproduction results. Neither file is rewritten merely to
mark a result stale, and v1 has no currentness cache.

Results no longer reachable from current `evidence.json` are ignored
immediately and contribute to no entry or log coverage. A later reproduction
publication may prune them. Ordinary `pyrun` and read-only reporting do not
rewrite results merely to remove them.

## Staging And Promotion

### Staging

Regenerated working outputs are disposable only when every output of their
execution matched. If any output is changed, missing, partial, failed to
compare, or otherwise incomplete, reproduction retains the complete available
execution output set, including matching siblings, partial outputs, captures,
and diagnostics.

The run directory is one of:

```text
<project>/tmp/reproduce-<log>-<run-id>/
<project>/tmp/reproduce-<log>-<entry>-<run-id>/
```

`<log>` and `<entry>` are stable normalized filesystem-safe identifiers. The
directory contains the durable run state and one
`research-log-reproduction-staging/1` manifest. Per-execution subdirectories
preserve output identities without collision. The manifest records run scope,
execution IDs, original output identities, availability, outcome, fingerprints,
and completeness.

Reproduction must never overwrite or delete a retained run directory or staged
bundle. There is no discard, cleanup, or supersede command. A researcher may
delete material directly from `<project>/tmp`.

### Promotion

Promotion is explicit and separate from reproduction:

```text
log reproduce promote --path LOG --run-id RUN_ID --execution-id EXECUTION_ID
```

The execution ID selects the complete indivisible output set recorded in
`pyrun.json`; individual artifact paths are not promotion selectors. Promotion
requires every output in the staged execution, verifies manifest, source
snapshot, recipe, output membership, staged fingerprints, and destination
preconditions, then copies the complete set into maintained locations. A
partial or stale set cannot be promoted.

Promotion copies; it never moves or modifies staged source files. Other
executions in the same run directory remain independently available. Missing
manually deleted staging material fails inspection or promotion clearly but
does not invalidate an already published reproduction result.

Promotion is a researcher-directed research mutation. It atomically updates
retained outputs and the related `pyrun.json`, `data.json`, evidence-dependent
state, reproduction state, and only the required targeted validation state. It
must not rerun validation generally. It leaves the staging bundle intact.

## Locking And Publication

### Scope Locks

Reproduction extends the existing lock implementation beneath
`<log>/.cache/research-log-operations/`; it must not introduce a second lock
system. One run holds exactly one scope lock for its complete active lifetime:
the selected entry lock for entry reproduction or the selected log lock for
log reproduction. It must not widen an entry run to dependency-entry locks or
a log run to every entry lock.

Before acceptance, a serialized active-target check rejects overlap:

- an entry conflicts with the same entry and its enclosing log;
- a log conflicts with itself and every entry in that log; and
- distinct entries in one log may run concurrently.

The prerequisite mutation guard must make maintained `log` mutations and
ordinary `pyrun` publication refuse changes protected by an active reproduction
entry or log lock. Raw filesystem edits and external origins do not participate
in advisory locks and remain covered by exact snapshot and fingerprint checks.

### Shared Publication

Concurrent distinct-entry runs share `reproduction/results.json`,
`reproduction.md`, validation confirmation state, and active-run indexing.
Their shared writes must use one brief log-local publication mutex built on the
existing lock infrastructure. It is not a reproduction scope lock and is not
held during planning, execution, or comparison.

Under the mutex, publication must reload current shared state, revalidate the
accepted snapshot boundary, merge only the completed target or lifecycle
record, append or update run history, perform the permitted targeted validation
refresh, compose the human report, and publish the coordinated bundle
atomically. It must detect conflicting concurrent or manual edits and preserve
the prior complete bundle on failure.

### Confirmation And Validation Refresh

When every output of an unconfirmed execution matches, reproduction may mark
that execution confirmed. The same coordinated publication must surgically
refresh only affected validation Provenance checks, dependency currentness,
scope aggregates, and their generated human projection. It must preserve every
unrelated validation check byte-for-byte at the structured-record level where
its dependency projection is unchanged.

The validation subsystem owns check identity, dependency projection,
aggregation, and the `validation/results.json` schema. Reproduction must call a
validation-owned targeted-refresh service rather than rewriting validation
records ad hoc. The service must prove that the validated source snapshot has
not otherwise changed and that its affected closure is complete. Any unexpected
change or incoherent refresh aborts the coordinated publication and requires a
separate ordinary validation run. Reproduction must never broaden the refresh
into general validation.

### Promotion Conflicts

Promotion acquires the producing entry's normal operation lock. It is rejected
while that entry or enclosing log is under reproduction and whenever an active
reproduction snapshot records a promoted artifact as an input. While active,
promotion publishes its complete output set in operation state so a newly
planned reproduction with an intersecting input snapshot is likewise rejected.
Its shared-state changes use the publication mutex.

## Human And Agent Interfaces

### Generated Files And Cutover

Reproduction owns:

```text
<log>/reproduction/results.json
<log>/reproduction.md
```

Validation continues to own `validation/results.json` and `validation.md`.
Cutover removes the legacy Reproduction result section from `validation.md`.
Validation may link to `reproduction.md` but must not duplicate reproduction
state.

Every maintained summary receives:

```markdown
Reproduction: [latest report](<log>/reproduction.md)
```

Cutover creates an empty authoritative result and a report stating that no
reproduction has yet completed. It must not infer a historical reproduction
result.

### Human Report

`reproduction.md` is deterministic, generated, nonauthoritative human output.
No researcher or agent edits it. One centralized compositor produces both the
file and the ready-to-present output of:

```text
log reproduce report --path LOG [--entry ENTRY]
```

The applicable CLI report must use the same counts, vocabulary, ordering, and
wording as the file projection. A reproduction agent presents it unchanged and
does not parse generated files or reconstruct a summary.

The report header contains only generation time, latest completed run, and
current artifact coverage counts. It has no aggregate pass/fail headline.

The current-state body has one section per entry in canonical log order. Each
heading contains the stable entry ID and human title and links to the exact
entry document. Unresolvable metadata falls back to the stable ID or logical
entry path without suppressing results. Each section contains:

| Artifact | Status |
| --- | --- |
| `data/result.csv` | matched |
| `images/result.png` | **changed** |

Artifact paths are relative to the entry when possible and ordered
deterministically. Every current artifact is shown, including matches. Every
status other than `matched`, including stale state, is bold. The report has no
detail limit or overflow omission: no non-matched or stale artifact may be
hidden.

After entry sections, a Runs table has exactly `Run ID`, `Target`, `Run
status`, `Time`, and `Folder`. It lists retained or availability-unknown runs
in deterministic reverse chronological order. An available directory is linked
by project-relative path.

When regenerating a report, history is pruned only if the applicable filesystem
and `<project>/tmp` parent are available and the exact run directory is
conclusively absent. An unavailable mount, broken or unavailable `tmp` target,
permission failure, or I/O error preserves the row and renders diagnostic
material unavailable. Removing run history never removes current artifact
results.

Human names, sentences, status labels, entry headings, and path presentation
come from one centralized catalog keyed internally by artifact outcome and
reason. The report must not expose internal reason codes, execution IDs,
fingerprints, or raw observed state. The public Run ID column is the intentional
exception.

### Bounded Artifact Queries

Agents diagnose current cases through:

```text
log reproduce artifacts list --path LOG [--entry ENTRY] [--outcome OUTCOME] [--artifact PATH]
log reproduce artifacts show --path LOG --entry ENTRY --artifact PATH
```

`list` returns at most 50 current artifact records in deterministic order and
always includes exact matched, returned, and omitted counts. Entry, outcome,
and artifact filters are exact and combinable. It accepts no glob, regular
expression, fuzzy match, pagination, or adjustable limit.

`show` returns the complete current structured result for one exact entry and
artifact path. It fails on zero or multiple matches rather than broadening the
selection.

Both commands read the latest completed published record as-is, expose its
result date, and fail precisely for absent, ambiguous, malformed, or unsupported
state. They never validate, reproduce, repair, publish, clean up, or write a
file. Run-specific diagnosis remains on `status --json`.

### Agent Monitoring

After launching a durable job, an agent may offer to create a scheduled status
check. It may create that task only after the user confirms. The scheduled task
reads deterministic status JSON and reports meaningful progress, failure,
completion, or required action. It never controls the job.

## Compatibility And Evolution

The cutover is atomic. `pyrun-outputs.json`, the legacy validation Reproduction
section, Markdown-derived reproduction recipes, and any transition readers are
not runtime compatibility surfaces.

The following changes require explicit version review:

- execution identity or canonicalization;
- schema field grammar or semantics;
- comparison dispatch or equality semantics;
- artifact outcome or reason vocabulary;
- run status or resume semantics;
- scope or authority boundaries; and
- publication or locking guarantees.

Numeric bounds may be revised from measured retained-corpus evidence without
changing semantic policy, but their versioned owner and compatibility effect
must be explicit. A new comparison family, non-exact comparison, multi-log
scope, agent equivalence judgment, or broader artifact registry is not an
implicit extension.

## Current Implementation Boundary

No runtime implementation currently conforms to this target specification.
Until cutover, `docs/research-log-mechanical-validator-spec.md` remains
authoritative for the active `pyrun-outputs.json`, validation, evidence,
`data.json`, and lock contracts. Implementation phases must update that
specification where validation-owned schemas or services change and must keep
the ownership boundary explicit rather than duplicating those contracts here.

Before the affected implementation begins, freeze the exact result and status
JSON fixtures. The fixed initial bounds and comparison dispatch are already
inserted. The reproduction plan owns the remaining implementation gates.
