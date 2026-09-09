# Research-Log Reproduction Specification

## Status And Authority

Status: active implementation specification. The serial reproduction workflow
and its maintained-log cutovers are complete. The Phase 19 version 3 parallel
scheduling implementation is complete; maintained-corpus exclusivity metadata
migration and integrated verification remain pending until their plan gates pass.

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
  graph traversal, non-automatic boundaries, cycles, and dry runs.
- [Durable Reproduction Jobs](#durable-reproduction-jobs) defines launch,
  status, stop, resume, recovery, and exit semantics.
- [Execution Safety](#execution-safety) defines run-local execution, network
  denial, write confinement, and worker ownership.
- [Artifact Comparison](#artifact-comparison) defines exact type-aware
  comparison, the explicit evidence-scoped exception, and defensive failure
  behavior.
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
| Execution-state file | `research-log-pyrun/v3` |
| Execution identity | `pyrun-exec/v1:<sha256>` |
| Standard environment | `pyrun-standard/v1` |
| Execution contract | `research-log-pyrun-execution/2` |
| Reproduction result | `research-log-reproduction-result/2` |
| Durable run state | `research-log-reproduction-run/3` |
| Run status projection | `research-log-reproduction-status/3` |
| Dry-run plan | `research-log-reproduction-plan/3` |
| Project scheduling coordinator | `research-log-reproduction-scheduler/1` |
| Exclusivity migration result | `research-log-pyrun-exclusivity-migration-result/1` |
| Exclusivity migration transaction | `research-log-pyrun-exclusivity-migration-transaction/1` |
| Source snapshot | `research-log-reproduction-source-snapshot/3` |
| Run-output manifest | `research-log-reproduction-staging/2` |
| Comparison dispatch | `research-log-reproduction-comparison/1` |
| Evidence-scoped comparison | `research-log-evidence-scoped-comparison/1` |
| Evidence-scoped result detail | `research-log-evidence-scoped-comparison-result/1` |

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
| Project scheduler record encoded bytes | 64 MiB |
| Active project scheduling permits | 4,096 |
| Waiting project exclusive tickets | 10,000 |
| Exclusivity migration result encoded bytes | 64 MiB |
| Exclusivity migration transaction encoded bytes | 64 MiB |
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
  the permitted execution scope or is excluded from automatic reproduction.
- **Scope lock:** the one existing research-log operation lock held for the
  selected entry or log throughout an active run.
- **Publication mutex:** the brief log-local lock used to serialize shared
  reproduction-result and report writes.
- **Execution reference:** the compound `{entry, execution_id}` identity of one
  planned execution. An execution ID alone is not unique across entries.
- **Scheduling permit:** one project-coordinated ordinary or exclusive grant
  held from immediately before worker launch until terminal attempt state is
  durable and no worker from that attempt survives.

## Authority And Boundaries

### Reproduction Authority

The operational authority is:

| Surface | Authority |
| --- | --- |
| Research-log Markdown | Human research account, evidence presentation, and explanatory command history |
| `evidence.json` | Reproduction roots and exact retained evidence-source identity |
| `data.json` | Named material location, fingerprint, and origin/generated classification |
| `pyrun.json` | Current executable recipes and observed execution state |
| `validation/results.json` and `validation/batches.json` | Reproduction admission result and per-chain projection |
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

Reproduction may change only its generated state and a confirmation-only field
in `pyrun.json`. After a completed run, it invokes ordinary validation as a
separate operation; validation alone owns its generated state. Reproduction
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

Before execution, `pyrun` holds the entry-operation lock and validates current
state. A malformed regular `pyrun.json` is preserved at the first unused
`pyrun.json.bak`, `pyrun.json.2.bak`, or later numbered backup. The runner reports
`pyrun.state.quarantined` with the backup and `repair_required:true`, then exits
without executing or creating replacement state. A symlink or non-file is
rejected without quarantine. A legacy `pyrun-outputs.json` requires migration
before another run.

### File Shape

The file is strict UTF-8 JSON with no duplicate keys, non-finite numbers,
unknown fields, or trailing content, and with one trailing newline. It has
exactly:

```json
{
  "schema": "research-log-pyrun/v3",
  "executions": {
    "pyrun-exec/v1:0123456789abcdef...": {
      "confirmed": true,
      "auto_reproduce": true,
      "exclusive": false,
      "last_run_at": "2030-01-01T00:00:00Z",
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
unique execution IDs. Every execution value has exactly `auto_reproduce`,
`exclusive`, `confirmed`, `last_run_at`, `runner`, `environment_profile`,
`execution_contract`, `recipe`, and `observed`.

`auto_reproduce`, `exclusive`, and `confirmed` are required Booleans.
`exclusive` states that reproduction must run the execution alone among all
managed reproduction executions in the current Git project. It is scheduling
policy, not a CPU, GPU, device, affinity, or external-process declaration.
`last_run_at` is either `null` or
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
- `parameters` is the exact ordered replay parameter vector. It contains each
  leading runner-owned stream-capture option and target, followed by `--`, then
  the exact ordered child-process argument tail after the script. Without a
  capture, it contains only that child-process argument tail. It contains no
  runner role declarations, `--auto-reproduce=false`, or explicit environment
  options.
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
and a Structure failure during validation.

A single noncanonical spelling that resolves to the output identity remains
mechanically bindable but is also a Structure failure. This lets `pyrun`
record the completed live invocation without inventing a second identity while
requiring the authored command to use the canonical spelling before
reproduction. The binding projection is derived wholly from `parameters` and
`outputs`, both already covered by the execution identity.

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

The projection includes the normalized script, ordered replay parameters,
explicit environment variables, direct input names, and complete output paths
and kinds. The replay parameters make each runner-owned stream capture and its
output identity explicit. It excludes observations, confirmation,
automatic-reproduction and exclusivity policy,
timestamps, Markdown location, standard-environment profile, schema version,
runner version, and execution-contract version.

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
and participate in identity. A missing project environment or required
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
attempts, reproduction execution, and confirmation-only or policy-only mutations
must not change it. Ordinary `pyrun` reads and writes only its entry-local
state; it must not load, scan, mark, or rewrite log-wide reproduction results.

An ordinary successful publication is confirmed. A complete unconfirmed
recipe remains runnable. Reproduction changes `confirmed` to true immediately
after every output in that execution has the `matched` outcome in one completed
attempt and the complete comparison has been durably recorded. That
confirmation-only mutation preserves the recipe, observations, policy,
versions, and `last_run_at`. It is not rolled back by a later execution,
reproduction-publication, or validation failure. Any changed, failed,
comparison-failed, or skipped output leaves it unconfirmed.

### Automatic-Reproduction Policy

`pyrun --auto-reproduce=false -- script.py ...` records
`auto_reproduce: false`. Omitting the option records `auto_reproduce: true`.
The false value marks work that must not be rerun automatically, such as
simulation or model training. It is an authored policy, not an inference from
measured duration. No other spelling or truthy/falsey value is accepted.

Automatic-reproduction policy is outside identity. A later policy-only change
uses:

```text
log pyrun update --path LOG --entry ENTRY --execution-id ID --auto-reproduce BOOL
```

`BOOL` is exactly `true` or `false`. The operation takes the selected entry
lock, changes only `auto_reproduce`, and writes atomically. The researcher or
authoring agent must first edit the Markdown option. The operation resolves the
supplied execution ID to one concrete expanded recipe and its containing
authored invocation, requires exact structural agreement apart from the
requested policy difference, and never edits Markdown.

If the authored invocation is one bounded static loop, the operation resolves
the complete expansion and atomically applies the same policy to every
distinct affected execution ID. It never merges those executions. Any other
Markdown-to-state disagreement is a refusal. The operation neither runs the
recipe nor refreshes validation; ordinary validation remains the separate next
authoring step.

### Exclusive-Scheduling Policy

`pyrun --exclusive -- script.py ...` records `exclusive: true`. Omitting
`--exclusive` records `exclusive: false`. No value-bearing or negative spelling
is accepted. The option affects managed reproduction scheduling only: ordinary
direct `pyrun` execution is unchanged, and the runner does not reserve CPUs,
GPUs, devices, host affinity, or unrelated host processes.

Exclusivity is outside execution identity. A later Markdown-first policy change
uses:

```text
log pyrun update --path LOG --entry ENTRY --execution-id ID --exclusive BOOL
```

`BOOL` is exactly `true` or `false`. `--exclusive` and `--auto-reproduce` are
mutually exclusive update selectors so one operation changes one policy. The
operation otherwise uses the same concrete-expansion agreement, entry locking,
identity preservation, and validation boundary as automatic-reproduction
policy updates.

### Exclusivity Metadata Migration

The one-time schema conversion uses:

```text
log pyrun migrate-exclusivity --path LOG [--dry-run] [--format text|json]
```

The operation accepts current strict `research-log-pyrun/v2` records and
already-converted `research-log-pyrun/v3` records. It parses every current
Markdown command and bounded loop expansion, requires exact agreement with
every stored recipe and existing automatic policy, and derives `exclusive`
only from the authored `--exclusive` option. It accounts for every stored and
authored execution; an orphan, ambiguity, unsupported command, or disagreement
aborts the complete log migration. It never reads the Phase 19 audit as machine
authority and never executes a recipe.

`--dry-run` is write-free and emits the same deterministic accounting as the
mutating form. JSON output uses
`research-log-pyrun-exclusivity-migration-result/1` and has exactly `schema`,
`summary`, `status`, `changed`, `totals`, `entries`, `executions`, and
`diagnostics`. `status` is `ready`, `complete`, or `refused`; `changed` is a
Boolean. `totals` has exactly `authored_invocations`, `expanded_executions`,
`stored_executions`, `converted_v2`, `unchanged_v3`, `exclusive_true`,
`exclusive_false`, and `unaccounted`, all nonnegative integers. Entry items have
exactly `entry`, `authored_invocations`, `expanded_executions`,
`stored_executions`, `exclusive_true`, and `exclusive_false`. Execution items
have exactly `entry`, `execution_id`, `script`, `markdown_path`, `line`,
`invocation_kind`, `expansion_index`, `prior_schema`, `prior_exclusive`,
`target_exclusive`, and `action`. `invocation_kind` is `direct` or
`loop_expansion`; `expansion_index` is null for direct commands and otherwise
the zero-based stable expansion index. `line` is the one-based opening line of
the containing shell command fence. `prior_exclusive` is null for v2 and a
Boolean for v3; `action` is `convert` or `unchanged`. Arrays use canonical log,
entry, Markdown-location, expansion, and execution-ID order. A ready or complete
result requires `unaccounted: 0` and no diagnostics. Text is a complete human
projection of the same object. Diagnostic items have exactly `code`, `message`,
`entry`, `execution_id`, `markdown_path`, and `line`; inapplicable identity and
location fields are null.

The mutating form takes the exclusive log operation lock, first recovers or
refuses recognized transaction residue, then repeats the complete
reconciliation. It stages every affected entry record beneath
`<log>/.cache/research-log-operations/pyrun-exclusivity-migration/`. The strict
`transaction.json` uses
`research-log-pyrun-exclusivity-migration-transaction/1` and has exactly
`schema`, `transaction_id`, `state`, `created_at`, and `entries`. `state` is
`prepared` or `committing`. Each stable-entry-order item has exactly `entry`,
`target`, `original_digest`, `staged`, `staged_digest`, and `published`.

After every staged v3 file and its directory are durable, the operation writes
the `prepared` journal. Replacing it atomically with `state: committing` is the
transaction commit point. Before that point, recovery removes intact staged
files and changes no target. After that point, recovery rolls forward in stable
entry order: a target matching `original_digest` receives its staged file, a
target matching `staged_digest` is marked published, and any other state is a
refusal that preserves the residue. Each successful replacement durably updates
`published`; completion requires every target to match its staged digest before
the residue is removed. Other operations that encounter recognized residue
refuse with its transaction ID and direct the caller to this migration command;
they never interpret an intermediate mixed schema. Mixed v2/v3 state without a
valid journal is an unexplained refusal.

The transaction preserves execution IDs, recipes, observations, confirmation,
automatic policy, version fields, and `last_run_at`. An already-converted log
must agree and is an idempotent no-op. An orphan, ambiguity, unsupported
command, disagreement, or unaccounted identity refuses before staging; migration
never repairs it.

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
log reproduce --path LOG [--entry ENTRY] [--include-all] [--recheck] [--jobs N] [--dry-run]
```

Omitting `--entry` selects exactly one complete log. Supplying `--entry`
selects exactly that stable entry. There is no multi-log, all-log, or
project-wide reproduction operation.

`--jobs` accepts a positive decimal integer and defaults to 1. It is the maximum
number of concurrently active executions in this run, not a promise that the
cap can be reached. Graph readiness, path conflicts, project-wide exclusive
coordination, and available work may reduce concurrency. The accepted value is
immutable; status, stop, resume, recovery, and publication cannot override it.

Evidence records inside the selected target define initial artifact cases.
Only artifacts reachable from those current evidence roots participate in
current coverage or execution planning.

### Admission Gate

Before accepting or previewing work, reproduction requires current completed
`validation/results.json` and `validation/batches.json` for the exact source
snapshot. Incomplete, malformed, unsupported, stale, or mutually inconsistent
validation state blocks the plan. Current findings are admitted per connected
same-entry command chain: Structure, Evidence, and failed Provenance findings
exclude only their affected batches. Independent batches remain eligible, and
ordinary dependency propagation prevents admitted downstream work from running
when it depends on an excluded upstream batch.

Unconfirmed Provenance and Hygiene findings do not block reproduction.
Unconfirmed runnable recipes are deliberately eligible so reproduction can
establish confirmation.

The validation subsystem's persisted batch projection determines finding
membership and admission. Reproduction must not reconstruct groups or classify
raw check failures independently. A selected execution output must map to
exactly one projected chain; a blocking unresolved group or ambiguous mapping
excludes the affected work. Summary provenance that depends on
`provenance.output.unconfirmed` remains a non-failing dependent check and
does not create an additional admission blocker. A summary-target
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

For a source outside the current Git project, the plan uses its exact authored
`data.json` location as the boundary or failure artifact identity. External
origins remain fingerprint-verified boundaries. An external non-origin source
is reported as `cross_log_generated_input`; it does not abort planning for
independent in-scope executions.

Every selected execution includes the complete inherited local Python code
dependency projection. Missing or changed participating code affects
admission, planning, currentness, and guarded resume exactly as the final
authoring contract requires.

### Non-Automatic Boundary

By default, planning stops before every required `auto_reproduce: false` execution. Its
retained output may serve as a boundary only when its current fingerprint and
required provenance state are valid. This boundary is planning metadata, not
an artifact outcome.

`--include-all` includes automatic and non-automatic executions within the same
selected entry or log boundary and traverses their upstream closure. It does
not widen the target or bypass validation. Scope is immutable after run
acceptance. The CLI must not prompt to widen it.

### Selection Policy

Incremental selection is the default. It selects only new, unconfirmed,
failed, stale, and dependency-affected eligible executions. A current result
otherwise satisfies its artifact case without new execution work.

`--recheck` selects every eligible execution in the current evidence-relevant
closure under the chosen entry-or-log target and automatic-reproduction policy, including
executions whose artifact results are already current. It preserves execution
grouping, dependency order, target boundaries, retained boundaries, and
artifact-level result identity. It does not bypass validation admission,
repair a graph failure, or make an otherwise ineligible case runnable.

All-execution inclusion and selection policy are independent. `--recheck`
alone stops at verified retained non-automatic boundaries. `--recheck
--include-all` also selects non-automatic executions. Neither flag implies the
other.

Recheck is a launch-time planning input. The emitted plan records the exact
selected cases and executions and is the durable authority for execution and
resume. Plan, run, status, and cumulative-result JSON therefore gain no
selection-policy field, schema version, or migration. Commands that consume an
accepted run or only query published state do not accept `--recheck`.

### Failures And Parallel Ordering

Missing or multiple producers, invalid boundaries, resource-limit violations,
and cycles are mechanical artifact failures. A reachable dependency cycle
fails every affected component member with reason `dependency_cycle`; no
execution in the cycle runs. Independent acyclic components may continue.
Artifacts not attempted after a required upstream failure are `skipped` with
reason `dependency_failed`.

The planner groups cases by execution reference, assigns every execution one
stable topological order, and preserves artifact-level result identity. Runtime
uses that order as the ready-queue tie breaker; completion order never changes
the plan, case ordering, or publication ordering. An execution becomes ready
only after every selected dependency has terminal durable state. A failed
execution blocks only its transitive dependents with `dependency_failed`;
independent ready work continues.

The scheduler launches no ready execution that conflicts with running managed
work. Each execution has canonical read, write, run-directory, and
runner-writable claims derived from the accepted recipe and run layout. Two
executions conflict when their output sets overlap, one writes an input read by
the other, their run directories coincide, or their writable claims overlap or
contain one another. Shared read-only inputs do not conflict. Claim comparison
uses resolved normalized paths and rejects unsafe aliases rather than guessing.
The run admits at most `jobs` active execution references at once.

An execution with `exclusive: true` additionally requires the project-wide
exclusive permit defined in [Project-Wide Scheduling](#project-wide-scheduling).
Once any exclusive execution is ready, its durable waiter prevents new ordinary
admissions project-wide. Existing ordinary work drains, the oldest stable
exclusive waiter runs alone, and later ordinary arrivals cannot starve it.
Multiple exclusive waiters use the coordinator's monotonic ticket, run ID, and
stable plan order as their deterministic priority tuple.

The executor must persist a producer's terminal checkpoint and output
availability before releasing its scheduling permit or making dependents ready.
Worker completion observed only in memory is insufficient. A failed producer's
durable checkpoint similarly precedes dependent skips.

An input beneath a declared directory output depends on that directory's
producer just as an exact file output does. If that producer fails, the
consumer is skipped with `dependency_failed`; the missing regenerated member
must not abort independent work in the run.
Its default incremental policy selects all and only new, unconfirmed, failed,
stale, and dependency-affected current cases required by the target. It must
not infer a reduced plan from prior matches when a current dependency
invalidates them.

Graph node, edge, depth, execution, and projection limits are fixed and
code-owned in [Fixed Resource Bounds](#fixed-resource-bounds). Exceeding a
limit fails the affected planning operation; it never silently narrows the
graph.

### Frozen Scheduling Fixtures

The controlled contract fixture at
`skills/research-logging/tests/fixtures/parallel-reproduction-contract.json`
defines the minimum synthetic scheduling cases: two independent commands under
`jobs: 2`; a failed producer whose dependent is skipped while independent work
completes; and an exclusive waiter across two runs that closes ordinary
admission, drains active work, runs alone, and releases the queued ordinary
execution. Implementation tests may add cases, but must preserve these fixture
names and outcomes. The fixtures execute no maintained research command.

### Dry Run

`--dry-run` applies the same admission, discovery, graph construction, automatic
policy, incremental-or-recheck selection, and safety preflight as a real
launch. It emits one
deterministic `research-log-reproduction-plan/3` projection with exactly
`schema`, `summary`, `target`, `include_all`, `jobs`, `validation_snapshot`,
`source_snapshot`, `cases`, `executions`, `boundaries`, and `failures`.

`target` follows the target grammar below. Cases are sorted by canonical log
entry order and artifact path. Each case has exactly `entry`, `artifact`,
`execution_id`, `disposition`, and `reason`; `disposition` is `run`, `current`,
or `failed`, and `reason` is null only when no qualification is needed.

Executions are in deterministic run order and each has exactly `order`,
`entry`, `execution_id`, `depends_on`, `outputs`, `auto_reproduce`, `exclusive`,
`read_paths`, `write_paths`, `run_path`, and `writable_paths`. The four path
claim fields are the immutable normalized scheduling projection; path arrays
are sorted and unique. Before a run ID exists, run-local claims use the
`<run>/...` portable prefix and project paths use `<project>/...`; acceptance
resolves them beneath the chosen canonical run and project roots without
changing their identity or creating dry-run state. `depends_on` and `outputs`
are sorted unique identity arrays. A dependency reference is the
entry-qualified string `<entry>:<execution_id>` because the same stable recipe
identity may legitimately occur in more than one entry; `execution_id` itself
remains exactly the ID recorded in that entry's `pyrun.json`. Boundaries are
sorted and each has
exactly `kind`, `entry`, `name`, `artifact`, and `fingerprint`; `kind` is
`origin`, `cross_entry`, or `non_automatic`. Fields inapplicable to a boundary kind are
null rather than omitted. Failures are sorted artifact projections with exactly
`entry`, `artifact`, `outcome`, `reason`, and `dependencies`.

The validation snapshot records `result_path`, `result_date`, `rules_version`,
`result_digest`, `source_projection_digest`, `projection_path`,
`projection_digest`, `validation_id`, and `batch_admission`. The admission
projection uses `research-log-reproduction-batch-admission/1` and lists every
admitted chain plus every excluded chain with its blocking finding IDs. The two
file digests cover the exact completed result and batch projection;
`source_projection_digest` covers the validation-owned complete research-source
projection. Reproduction treats these values as immutable currentness tokens.

The source snapshot uses
`research-log-reproduction-source-snapshot/3` and has exactly `schema`,
`authority_files`, `executions`, and `materials`. `authority_files` records the
canonical path and SHA-256 bytes of every `evidence.json` and `data.json` loaded
for the plan. `executions` records each selected execution ID and the SHA-256
digest of its canonical execution record after omitting only the mutable
`confirmed` field. `materials` records every current script, participating code
file, direct input, retained boundary, and comparison baseline by canonical
identity, role, kind, and closed fingerprint. All arrays are unique and
canonically sorted.

At acceptance, the CLI verifies the validation result, batch projection, and
complete source snapshot. At execution, resume, and final reproduction-
publication boundaries it rechecks the accepted validation files and source
snapshot; comparison and confirmation remain inside the same accepted scope
lock. This permits the run's own confirmation writes while still rejecting any
change to a recipe, observation, policy, input, script, code path, data
declaration, evidence root, comparison baseline, or admitted batch decision.

Dry run is completely write-free. It creates no run ID, lock, output workspace,
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
CLI. It is immutable and names the durable state, output workspace, diagnostics,
and staging paths for the life of the run. It is not derived from Markdown or
an execution recipe.

The accepted target, entry-or-log kind, all-execution inclusion policy, and
`jobs` value are immutable.
Management commands use only the recorded scope:

```text
log reproduce status --path LOG --run-id RUN_ID [--json]
log reproduce stop --path LOG --run-id RUN_ID
log reproduce resume --path LOG --run-id RUN_ID
```

They must reject `--entry`, `--include-all`, and `--jobs`.

### Durable State

Each run directory contains one canonical `run.json` using
`research-log-reproduction-run/3`. Its top-level object has exactly:

```json
{
  "schema": "research-log-reproduction-run/3",
  "run_id": "reproduce-...",
  "summary": "docs/research.md",
  "target": {"kind": "entry", "entry": "e003"},
  "include_all": false,
  "jobs": 2,
  "source_snapshot": {},
  "validation_snapshot": {},
  "plan": {},
  "state": {
    "status": null,
    "phase": "executing",
    "active_executions": [
      {"entry": "e003", "execution_id": "pyrun-exec/v1:..."},
      {"entry": "e004", "execution_id": "pyrun-exec/v1:..."}
    ],
    "latest_execution_diagnostic": null,
    "operational_failure": null
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
    "run": "tmp/reproduction/2030-01-01/reproduce-research-e003-reproduce-...",
    "workspace": "workspace",
    "diagnostics": "diagnostics",
    "staging": "executions"
  },
  "workers": [
    {
      "worker_id": "worker-12347",
      "parent_worker_id": null,
      "pid": 12347,
      "entry": "e003",
      "execution_id": "pyrun-exec/v1:...",
      "state": "running",
      "registered_at": "2030-01-01T00:00:01Z",
      "last_observed_at": "2030-01-01T00:00:02Z"
    }
  ],
  "checkpoints": []
}
```

`target` has exactly `kind` and `entry`. `kind` is `entry` or `log`; `entry`
is the stable entry ID for an entry target and null for a log target.
The top-level `jobs` value must equal the immutable value in `plan`.
`source_snapshot` and `validation_snapshot` are byte-for-byte the projections
defined by dry-run planning. `plan` is the accepted
`research-log-reproduction-plan/3` object without its outer `schema` and must
not change after acceptance.

`state.status` is null while active and otherwise one terminal status:
`complete`, `stopped`, or `failed`. `state.phase` is one of `accepted`,
`planning`, `preflight`, `executing`, `comparing`, `publishing`, `stopping`, or
null; it is null in terminal state. `active_executions` is the stable-plan-order
array of every execution reference with a live scheduling permit and is empty
otherwise. Its length never exceeds `jobs`; an exclusive item is always the
only member.
`latest_execution_diagnostic` is null or the latest execution-level failure or
stop diagnostic. `operational_failure` is null unless a run-level error
prevents reproduction from reaching its completed publication endpoint. Each
non-null diagnostic has exactly `code`, `message`, `entry`, `execution_id`, and
`recorded_at`; `entry` and `execution_id` are both null for a run-level
diagnostic. A complete run always has a null
`operational_failure`, even when one or more artifact outcomes are failures.

`progress` has exactly the fields shown. Every outcome count is a nonnegative
integer. `timestamps` has exactly the fields shown; absent lifecycle events are
null. Paths are normalized run-directory-relative paths except `run`, which is
project-relative. Worker and checkpoint arrays are sorted by their stable
identities.

Each worker item has exactly `worker_id`, `parent_worker_id`, `pid`, `entry`,
`execution_id`, `state`, `registered_at`, and `last_observed_at`.
`parent_worker_id`, `entry`, and `execution_id` may be null where their
relationship is not applicable. `state` is exactly `running` or `exited`.
Only `running` is live and may appear in `active_workers` or
`surviving_workers`; an `exited` record is retained in run history but does not
hold a permit. Each checkpoint item has exactly `entry`,
`execution_id`, `state`, `path`, `completed_at`, `started_at`, `finished_at`,
`elapsed_seconds`, `failure`, and `outputs`; `state` is `active`, `succeeded`,
`failed`, or `stopped`. `failure` is null for active and succeeded attempts and
otherwise has exactly `code`, `message`, and `recorded_at`. A stopped checkpoint
is the only resumable attempt state; failed is terminal and is never retried in
the same run. Fields unavailable in a state are null. Output entries use
canonical output identities and observed fingerprints. Timing begins at the first
supervised child launch. Elapsed time uses a monotonic clock and accumulates
only active supervised runtime. A stopped resumable attempt preserves its
first `started_at`, has no `finished_at`, and adds its resumed active interval
to `elapsed_seconds`.

The run record therefore durably retains:

- run ID, log, target kind, target entry when applicable, include-all policy,
  and jobs cap;
- accepted source and validation snapshots;
- immutable deterministic execution plan;
- run status, current phase, active executions, latest execution diagnostic,
  and operational failure;
- accepted, started, updated, stopped, resumed, and finished timestamps where
  applicable;
- completed and total execution counts;
- accumulated artifact-outcome counts;
- per-execution checkpoints and worker registrations;
- output-workspace, diagnostics, and staging paths; and
- stop, interruption, recovery, and publication state required for idempotent
  continuation.

Unknown fields fail. Checkpoint writes must be atomic and sufficient to
distinguish `succeeded`, `failed`, or `stopped` work from an `active` execution
after process or host failure. Cardinality and byte limits are defined in
[Fixed Resource Bounds](#fixed-resource-bounds) and do not weaken this state
contract.

### Status

Default status is concise human text. `--json` emits one deterministic
`research-log-reproduction-status/3` object containing exactly `schema`,
`run_id`, `summary`, `target`, `include_all`, `jobs`, `status`, `phase`,
`active_executions`, `active_workers`, `execution_timings`, `completed_executions`, `total_executions`,
`artifact_outcomes`, `timestamps`, `latest_execution_diagnostic`,
`operational_failure`, and `surviving_workers`. The values are the
corresponding strict projection of `run.json`.
`active_executions` uses the run-state execution-reference shape and order.
`active_workers` contains every current worker record associated with those
references and is empty when no execution is active.
`execution_timings` contains only launched executions, in stable plan order,
with exactly `entry`, `execution_id`, `state`, `started_at`, `finished_at`,
`elapsed_seconds`, and `failure`. These fields are the checkpoint timing,
terminal-state, and diagnostic projection. Planned-but-queued executions are absent, so status does not
misrepresent queue time as execution time.
`surviving_workers` is normally empty and, while stopping cleanup remains
incomplete, contains the exact sorted worker records still observed alive.

Agents and scheduled monitors must consume JSON rather than parse human text.

### Run And Artifact States

Terminal run statuses are:

- `complete`: the job reached its normal endpoint and reproduction-result
  publication succeeded;
- `stopped`: execution is not active, the run retains resumable same-path
  state, and the scope lock has been released; and
- `failed`: an operational failure prevented final artifact-result
  publication.

`stopping` is an active phase, not a terminal status. Artifact changes or
failures do not make a successfully published run operationally failed. A
complete run may contain any artifact outcome.
When run-level failure cleanup is still active, `phase: "stopping"` together
with a non-null `operational_failure` is the durable failed-terminal intent.
Recovery preserves that intent, finishes worker and permit cleanup, and then
publishes `status: "failed"`; it must not reinterpret the transition as a user
stop.

### Stop

`stop` is the sole user operation for ending active work without deleting it.
It cancels this run's project-scheduler waiters, signals every active supervised
worker tree for graceful shutdown, waits one fixed code-owned grace period, then
force-terminates every survivor. It does not wait for active executions to
finish naturally.

The run becomes `stopped` and releases its scope lock only after no supervised
worker or scheduling permit remains. It retains the same run ID, workspace path, checkpoints,
partial outputs, and diagnostics.

If forced termination leaves a survivor, the run remains active in `stopping`,
retains its lock, records exact survivor diagnostics, and the stop request
returns nonzero. Repeating `stop` retries the bounded cleanup.

### Resume

`resume` is available for `stopped` runs and for a `failed` run whose sole
operational failure is `reproduction.publication.failed`. It reacquires the
original scope lock and reuses the same run-local output workspace and run
paths and immutable `jobs` cap. A stopped run skips `succeeded` and `failed`
execution checkpoints and reinvokes only `stopped` executions in their original run paths,
preserving script-native checkpoint and resume behavior. It restores the stable
ready queue from the accepted plan and durable checkpoints, but does not reuse
an expired scheduling permit. A publication retry reuses every durable comparison, terminal `failed`
attempt, dependency skip, and `succeeded` checkpoint; it performs no second
research-command attempt.

Before executing, resume must verify exact agreement with the recorded recipes,
scripts, participating code, inputs, and retained comparison artifacts. Any
difference refuses resume without deleting the old run; a new reproduction run
is required.

### One-Attempt Rule

Within one reproduction run, each compound `(entry, execution_id)` is attempted
at most once. Multiple artifact cases and dependent branches reuse that one
terminal result. A failed execution remains failed, its dependents are skipped
with `dependency_failed`, and independent executions continue. Resuming a
stopped execution at the unchanged run path continues the same attempt; it is
not a second attempt. Only a separately requested reproduction run may create a
new attempt after an execution has terminally failed.

### Recovery

Host or supervisor recovery performs reconciliation and worker cleanup only. It
must never restart research execution automatically. Every formerly active run
is reconciled, surviving registered workers receive the same bounded cleanup,
and its project-scheduler waiters and permits are reconciled under the scheduler
mutex. The run becomes reason-coded `stopped` only after no worker or permit
remains, except that a `stopping` run with non-null `operational_failure`
preserves that durable intent and becomes `failed` after cleanup. The scope lock
is not released earlier. Execution continues only after explicit `resume`
passes ordinary guards.

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

### Run-Local Output Workspace

Every job executes retained scripts and participating code directly from their
current verified locations under read-only confinement. It does not copy the
project. Each run owns an initially empty output workspace that mirrors only
the project-relative, log-relative, and entry-relative directories required by
generated paths.

Every ordinary declared output must bind unambiguously to exactly one recorded
child-parameter occurrence. Runner-owned captures are direct bindings. Before
execution, the executor substitutes each binding with the corresponding path
inside the run workspace. Current clean Structure validation is an admission
requirement and therefore prevents a recipe with a missing, ambiguous, or
noncanonical binding from reaching execution. The executor consumes the same
shared binding projection defensively; an unexpected projection failure means
the validated source snapshot changed or an implementation invariant failed,
not a separate artifact outcome or user-facing binding check.

The executor resolves retained origins and boundaries directly from their
verified read-only locations. When a downstream input is the output of an
earlier selected execution, it substitutes the regenerated path from the same
run workspace. Comparison reads the retained artifact as its immutable
baseline. No retained input or baseline is copied into the workspace.

Recipes execute from the workspace's mirrored entry directory. The executor
uses the project-local Python environment and recorded execution environment.
Runner-owned temporary and cache locations, including `MPLCONFIGDIR` and
`XDG_CACHE_HOME`, are located inside the run's allowed paths.

For parallel execution, each attempt receives a distinct mirrored entry run
directory and runner-temporary root. Its writable confinement is exactly that run directory, declared
output targets outside that directory, runner capture targets, and its private
temporary and diagnostic roots. Those paths form the accepted `run_path`,
`write_paths`, and `writable_paths` claims. Regenerated dependencies are made
read-only to consumers after their producer checkpoint is durable. The
supervisor, not a child process, performs any atomic materialization into a
shared dependency location. Two independent executions in the same entry may
therefore run concurrently when their logical output, input, and writable
claims do not conflict; a resumed `stopped` attempt reuses its original run path.

Generated outputs, temporary files, checkpoints, captures, and diagnostics are
confined to the attempt's declared run-owned paths. Retained scripts, participating code, inputs,
boundaries, comparison baselines, and the project-local environment remain
read-only. A script that accepts but ignores a substituted output and attempts
another write fails at runtime; static inspection never substitutes for this
control.

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

Comparison is automatic, code-only, bounded, and versioned. No agent or
researcher decides equivalence during a run. Whole-artifact type-aware exact
comparison is the default. One generated file may explicitly select the
evidence-scoped exception defined below; no exception is inferred from format,
name, execution, or an observed difference.

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
comparison cannot complete has outcome `comparison_failed` with a precise
reason such as `resource_limit`, `unsupported_format`, or `comparator_error`.
It and every available sibling output are retained for diagnosis.

### Evidence-Scoped Comparison

A generated file may opt into `research-log-evidence-scoped-comparison/1`
through its `data.json` item:

```json
"comparison": {
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
selected value is `changed`. A missing value, invalid or incompatible
selection, failed transformation, or other inability to evaluate the declared
contract is `comparison_failed` with reason
`evidence_comparison_failed`. Regenerated output remains retained under the
ordinary run policy.

The definition identity covers the artifact declaration plus the complete
applicable evidence records, including sources, locators, transformations, and
tolerances. A relevant `data.json` or `evidence.json` change therefore makes a
prior artifact result stale. Evidence-scoped comparison is introduced only for
one reviewed legitimate nondeterministic artifact at a time after researcher
approval of its evidence set and smallest scientifically justified tolerance;
it is never populated across the corpus automatically.

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
non-automatic or permitted cross-entry dependency is boundary metadata rather than an
artifact outcome. When the selected evidence root itself is non-automatic or is
produced outside an entry target, that selected artifact is respectively
`skipped` with reason `non_automatic` or `outside_entry`.

The complete v1 reason vocabulary is `baseline_unavailable`,
`boundary_changed`, `boundary_unavailable`, `comparator_error`,
`content_changed`, `cross_log_generated_input`, `dependency_cycle`,
`dependency_failed`, `evidence_comparison_failed`, `execution_failed`,
`generation_failed`, `graph_limit`, `missing_input`, `missing_producer`,
`multiple_producers`, `output_missing`,
`outside_entry`, `resource_limit`, `safety_failure`, `non_automatic`, `stop_requested`,
`unsupported_format`, `worker_cleanup_incomplete`, and `worker_survived`.

### Authoritative Result

`<log>/reproduction/results.json` is strict canonical UTF-8 JSON using
`research-log-reproduction-result/2`. It has exactly this shape:

```json
{
  "schema": "research-log-reproduction-result/2",
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
      "include_all": false,
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
      "executions": [
        {
          "entry": "e003",
          "execution_id": "pyrun-exec/v1:...",
          "started_at": "2030-01-01T00:00:01Z",
          "finished_at": "2030-01-01T00:04:59Z",
          "elapsed_seconds": 298.4
        }
      ],
      "folder": {
        "path": "tmp/reproduction/2030-01-01/reproduce-research-e003-reproduce-...",
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
`profile`, `expected`, and `regenerated`, except that an evidence-scoped
artifact also has the complete group `evidence_contract`,
`evidence_definition`, and `evidence`. The definition is a SHA-256 identity;
the evidence array retains every record identity, retained and regenerated
selection projections, tolerance, and match result. On the exact
whole-artifact fast path the group is present with an empty evidence array.
`expected` and `regenerated` are the
closed observed fingerprint forms; a comparison failure that could not observe
one side uses null for that side. Detailed differences and decoder diagnostics
otherwise remain in the run directory.
`execution_id` is null only for a pre-execution graph failure that has no
resolvable producer; `matched` and `changed` always identify an execution.
Generated-output artifact identities are normalized entry-relative or
`<project>/...` paths. A pre-execution failure or boundary for a declared
resource outside the project retains its canonical absolute POSIX identity so
the result identifies the same resource as `data.json`; noncanonical absolute
forms remain invalid.

Every run item has exactly the fields shown. Its `executions` array records one
explicit timing projection for each launched attempt in accepted execution
order. Planned work that never launched has no timing item. Timing is
diagnostic only: it does not affect identity, currentness, selection,
comparison, or confirmation. Its target follows the run-state
target grammar. `status` is `complete`, `stopped`, or `failed`; an active run is
read through status and is added to the published index only when a lifecycle
event safely publishes it. `finished_at` is null for a resumable stopped run.
Outcome counts use all five required keys. `folder.path` is the normalized
project-relative run directory; `availability` is `available` or `unknown`.
A conclusively absent directory causes the whole run item to be removed rather
than persisting an `absent` value. Current artifact records retain their run ID
after that historical run item is removed; run-directory retention is not a
precondition for retaining the authoritative artifact outcome.

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

A stopped run or an operational failure before final reproduction publication
leaves the current artifact map unchanged. Confirmations already written for
matched executions remain intact. A publication failure may be retried from
the durable run-output manifest through the guarded resume route without
rerunning terminal execution attempts. Terminal lifecycle events may still
update the run index and human Runs table without publishing partial artifact
outcomes.

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

For evidence-scoped artifacts, currentness also requires the recorded
evidence-definition identity to equal the identity derived from current
`data.json` and `evidence.json`. A mismatch is stale as
`comparison_changed`, even when the producing execution has not rerun.

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
directory, and the accepted run directory. A dry run or read-only lookup
creates none of them. Existing-run lookup takes only the immutable run ID and
scans the immediate date directories for the exact matching leaf. Zero matches
is not found; more than one match is an integrity failure. There is no date
argument, persistent run index, or legacy-path lookup.

The directory contains the durable run state, one project-layout `workspace/`,
and one `research-log-reproduction-staging/2` manifest. The historical
filename `staging.json` is retained for compatibility, but the v2 manifest is
a durable comparison and run-output index rather than a copied staging bundle.
Each execution record contains exactly `bytes`, `complete`, `diagnostics`,
`entry`, `execution_id`, `outputs`, and `path`; `path` is `workspace`. Each
output records its artifact identity, declared kind, availability, exact
workspace-relative path, outcome and reason, selected comparison profile, and
retained and regenerated fingerprints. The full record is written atomically
before any matching confirmation.

Reproduction must never overwrite or delete a retained run directory or staged
bundle. There is no discard, cleanup, or supersede command. A researcher may
delete material directly from `<project>/tmp/reproduction`.

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

### Project-Wide Scheduling

Project-wide ordinary and exclusive permits use the existing operation-lock
implementation at the current Git project root, alongside rather than replacing
entry and log scope locks. A brief project scheduler mutex protects one bounded
generated coordinator record beneath the project operation-state directory.
The record contains waiting exclusive tickets and active permits with run ID,
execution reference, permit kind, supervisor identity, stable priority, and
normalized path claims. It is coordination state, not research state or a
reproduction result.

The coordinator path is
`<project>/.cache/research-log-operations/reproduction-scheduler.json`; its
mutex is `reproduction-scheduler.lock` in the same operation-state directory.
The strict canonical record has exactly:

```json
{
  "schema": "research-log-reproduction-scheduler/1",
  "next_ticket": 4,
  "waiters": [
    {
      "ticket": 3,
      "run_id": "reproduce-...",
      "entry": "e003",
      "execution_id": "pyrun-exec/v1:...",
      "plan_order": 7,
      "supervisor_pid": 12345,
      "registered_at": "2030-01-01T00:00:02Z"
    }
  ],
  "active": [
    {
      "permit_id": "permit-...",
      "kind": "ordinary",
      "run_id": "reproduce-...",
      "entry": "e004",
      "execution_id": "pyrun-exec/v1:...",
      "plan_order": 2,
      "supervisor_pid": 12346,
      "read_paths": ["/project/docs/research/.../data/input.csv"],
      "write_paths": ["/project/tmp/reproduction/.../data/output.csv"],
      "run_path": "/project/tmp/reproduction/.../entries/e004",
      "writable_paths": ["/project/tmp/reproduction/.../runtime/e004/..."],
      "granted_at": "2030-01-01T00:00:03Z"
    }
  ]
}
```

`next_ticket` is a nonnegative monotonically increasing integer within the
record. Waiters are sorted by `(ticket, run_id, plan_order)` and active permits
by `(run_id, plan_order, entry, execution_id)`. Each item has exactly the
fields shown. An exclusive active permit has `kind: "exclusive"` and is the
only active item. Paths are absolute normalized resolved paths used only for
same-project scheduling conflict checks. Empty state is removed once no active
or waiting run can reference it; the mutex path remains ordinary operation-lock
state.

An ordinary permit is admitted only when it conflicts with no active permit and
no exclusive ticket is waiting. An exclusive execution first records its ticket,
which closes ordinary admission, then waits without holding the scheduler mutex.
After active permits drain, the first `(ticket, run_id, plan_order)` tuple
becomes the sole active exclusive permit. A later ticket cannot overtake it. Scheduling never reserves
unrelated host processes or coordinates projects that do not share the current
Git root.

The lock order is scope lock, scheduler mutex, run-state lock, entry-local
confirmation lock, log publication mutex. No code may acquire an earlier lock
while holding a later one. The scheduler mutex is never held while waiting for
capacity, running or stopping workers, comparing artifacts, publishing results,
or invoking validation. A transition that touches coordinator and run state
takes the locks in that order and writes idempotent state so reconciliation can
finish either side after interruption.

A scheduling permit is released only after the attempt's `succeeded`, `failed`,
or `stopped` checkpoint is durable and every registered worker is gone. An incomplete stop
or recovery retains the active permit and scope lock while a worker survives.
A stopped waiter removes its ticket before releasing its scope lock. Recovery
reconciles coordinator entries against strict run state and live supervised
process identity; it may remove a proved-dead waiter or permit but never infer a
completed attempt or launch work. Coordinator corruption or unavailable process
inspection is an operational refusal, not permission to bypass exclusion.

Existing `research-log-reproduction-run/2` jobs are never rewritten. Their
original serial supervisor, status/2 projection, stop, resume, recovery, and
publication semantics remain available through a version-dispatched
compatibility path. Before accepting the first v3 run, and before every later
v3 launch while v2 state exists, the CLI performs a bounded project run scan.
An active v2 run with its pre-upgrade supervisor or any unreconciled worker
blocks v3 acceptance with `reproduction.scheduler.legacy_active`; the CLI does
not attempt retroactive enrollment. A v2 run started or explicitly resumed by
the new implementation acquires one conservative project-wide exclusive permit
for each remaining execution so it cannot overlap v3 managed work.

A stopped v2 run remains resumable only while its original source snapshot
still matches byte-for-byte under the v2 decoder. Exclusivity migration changes
that snapshot and therefore makes such a resume stale; it refuses normally
rather than projecting v3 state back into v2. Status, stop, recovery, report,
and retained diagnostics remain readable after migration.
Existing `research-log-pyrun/v2` execution records remain readable by ordinary
non-exclusive `pyrun`, automatic-policy updates, validation, migration, and
reproduction with `--jobs 1`. Such reproduction projects every selected v2
execution as conservatively exclusive; `--jobs` greater than one is refused.
These compatibility operations preserve v2 and never add or guess the missing
field. Only `migrate-exclusivity` writes v3; direct or policy invocations that
request exclusivity refuse v2 with a migration-required diagnostic.

### Shared Publication

Concurrent distinct-entry runs share `reproduction/results.json`,
`reproduction.md`, and active-run indexing. Their shared writes must use one
brief log-local publication mutex built on the existing lock infrastructure.
It is not a reproduction scope lock and is not held during planning, execution,
comparison, or per-execution confirmation.

Under the mutex, publication must reload current shared state, revalidate the
runtime source-snapshot boundary, merge only the completed target or lifecycle
record, append or update run history, compose the human report, and publish the
two reproduction-owned files atomically. It must detect conflicting concurrent
or manual edits and preserve the prior complete reproduction bundle on failure.
It never reads or writes validation state.

### Confirmation And Post-Reproduction Validation

When every declared output of an unconfirmed execution matches, reproduction
atomically changes only that execution's `confirmed` field in its entry-local
`pyrun.json`. The run already holds the owning entry or log scope lock. Each
confirmation is independent and durable; no later execution or publication
outcome rolls it back.

After reproduction-result publication succeeds and the run becomes complete,
the supervisor releases its reproduction scope lock and invokes ordinary
mechanical validation for that log as a separate operation. Validation owns
and publishes `validation/results.json`, `validation/batches.json`, and
`validation.md`; reproduction never
performs a targeted confirmation refresh. Validation findings or an
operational validation failure do not change the complete reproduction status,
confirmations, or reproduction results.

Distinct overlapping entry runs share the log lock and may finish close
together. Ordinary validation uses the existing exclusive log-operation lock:
earlier finishers defer when another reproduction still holds a shared log
lock, and the last finisher performs the single validation run after all such
reproduction work has ended. Reproduction and validation outcomes remain
separately visible.

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

Validation continues to own `validation/results.json`,
`validation/batches.json`, and `validation.md`.
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
publication time, and fail precisely for absent, ambiguous, malformed, or
unsupported state. They never validate, reproduce, repair, publish, clean up,
or write a file. Run-specific diagnosis remains on `status --json`.

### Agent Monitoring

After launching a durable job, an agent may offer to create a scheduled status
check. It may create that task only after the user confirms. The scheduled task
reads deterministic status JSON and reports meaningful progress, failure,
completion, or required action. It never controls the job.

## Compatibility And Evolution

The original execution and reproduction cutover is complete. Ordinary `pyrun`
and Reproduce require `pyrun.json`; neither executes legacy
`pyrun-outputs.json` records or derives reproduction recipes from Markdown. The
legacy validation Reproduction section is not a current report surface.

Parallel scheduling introduces `research-log-pyrun/v3` and reproduction plan,
run, and status version 3. Version 2 execution records and accepted runs use
only the bounded compatibility paths defined above. No accepted run is upgraded
in place, and no consumer may decode a v2 object with v3 defaults. The
exclusivity migration is complete only after every maintained log has atomically
converted and passed ordinary validation.

Mechanical validation retains a read-only legacy output-record reader and an
internal output-keyed projection of current execution state. That bounded
compatibility path is defined in the
[mechanical-validator specification](research-log-mechanical-validator-spec.md#pyrun-output-support-records).
It does not authorize legacy recording or reproduction. Migration converts
legacy records to current execution state before another run.

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

The command-oriented version 2 execution state, migration, serial planning,
safety, run-local execution, exact and evidence-scoped artifact comparison, durable comparison
records, immediate confirmation, independent result publication, current
projection, bounded read-only queries, durable job control, stop and same-path
resume, publication retry, lost-supervisor reconciliation, ordinary
post-reproduction validation, and whole-execution copy-based promotion are
implemented. The version 3 parallel-scheduling contract is frozen; its
implementation and maintained-corpus exclusivity migration remain pending.
Promotion retains its own approved targeted Evidence and
Provenance refresh without running general validation; reproduction has no
targeted-validation path. Maintained-corpus initialization and the bounded
entry-level cutover evaluation are complete. Full maintained-corpus
reproduction remains gated by the reproduction plan. The frozen result and
status fixtures remain the compatibility boundary.
