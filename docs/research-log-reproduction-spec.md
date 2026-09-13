# Research-Log Reproduction Specification

## Status And Authority

Status: active implementation specification. The serial reproduction workflow
and its maintained-log cutovers are complete. The Phase 19 version 3 parallel
scheduling implementation and maintained-corpus exclusivity metadata cutover
are complete.

This document is the normative implementation contract for mechanical
research-log reproduction, the command-oriented `pyrun.json` record, durable
reproduction jobs, comparison, publication, and promotion. Code, tests,
generated records, public commands, and agent-facing projections must conform
to it.

The [mechanical reproduction concept](../tmp/research-log-pyrun-reproduction-concept.md)
and completed [reproduction plan](../tmp/research-log-reproduction-plan.md)
provide historical rationale and implementation context, not current migration
instructions. This specification owns the durable runtime contract; active
implementation plans own only their authorized sequencing, verification, and
completion gates. It does not teach researchers how to use the workflow;
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
- [Current Contract Cutover](#current-contract-cutover)
  distinguishes the one-time data/evidence conversion from retained
  execution-state compatibility and the bounded promotion adapter.
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
| Execution-state file | `research-log-pyrun/v6` |
| Execution identity | `pyrun-exec/v2:<sha256>` |
| Standard environment | `pyrun-standard/v1` |
| Execution contract | `research-log-pyrun-execution/2` |
| Shared result store | `<log>/.cache/results.sqlite`, SQLite `user_version=15`; the physical shared schema is owned by the [mechanical-validator specification](research-log-mechanical-validator-spec.md#retained-validation-results) |
| Reproduction result projection | `research-log-reproduction-result/11` |
| Per-log summary | `research-log-reproduction-summary/5` |
| Cross-log summary | `research-log-reproduction-root-summary/5` |
| Durable run store | run-local `state.sqlite`, SQLite `user_version=2` |
| Run status projection | `research-log-reproduction-status/7` |
| Accepted plan | `research-log-reproduction-plan/10` |
| Command list | `research-log-reproduction-command-list/3` |
| Command detail | `research-log-reproduction-command/3` |
| Project scheduling coordinator | `reproduction-scheduler.sqlite`, SQLite `user_version=2` |
| Comparison dispatch | `research-log-reproduction-comparison/1` |
| Evidence-scoped comparison | `research-log-evidence-scoped-comparison/1` |
| Evidence-scoped result detail | `research-log-evidence-scoped-comparison-result/1` |
| Isolated repair-check result | `research-log-repair-check-result/1` |

Reproduction uses durable run-local state, status/7, and the consolidated
result-store schema. Older job files are
immutable but unsupported: the CLI reports `reproduction.run.unsupported` and
directs the caller to start a new current-format run.

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

### Cumulative Results

| Resource | Limit |
| --- | ---: |
| Consolidated result-store data per domain | 64 MiB |
| Current artifact records | 10,000 |
| Current command records | 10,000 |
| Retained or availability-unknown run records | 10,000 |

History pruning follows the filesystem-availability rules below. Reaching a
result limit is an explicit publication failure; it does not authorize
discarding available run history or current artifact state.

## Terminology

- **Execution recipe:** the normalized structural information required to
  invoke one child process and associate its direct inputs and complete output
  set.
- **Authored CID token:** an explicit `--cid VALUE` runner option. A canonical
  positive integer is a numeric shorthand; any other valid value is a full CID.
- **Derived CID:** the valid command ID obtained from a Python program's lexical
  filename without its `.py` suffix when `--cid` is absent.
- **Command ID (CID), or effective CID:** the full authored CID, the derived
  program stem plus `-N` for numeric shorthand `--cid N`, or the derived CID
  when `--cid` is absent. It is the stable entry-unique owner shared by every
  expansion of one command or loop.
- **Execution ID:** the stable `pyrun-exec/v2:<digest>` identity of one expanded
  child-parameter vector within a CID.
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
- **Execution reference:** the compound `{entry, cid, execution_id}` identity
  of one planned execution. An execution ID alone is not unique across CIDs or
  entries.
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
| `data.json` | Named material location, declaration identity, and origin/generated classification |
| `pyrun.json` | Current executable recipes and observed execution state |
| `.cache/results.sqlite` validation domain | Reproduction admission result and per-chain projection |
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
its retained inputs, script/code, and outputs; altered retained material
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
  "schema": "research-log-pyrun/v6",
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
            "code": {
              "scripts/helpers.py": {"algorithm": "sha256", "digest": "..."}
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
  directly executed script to its pre-launch fingerprint, using the static
  local-code-dependency path, stability, and warning rules owned by the
  mechanical validator specification. Historical runtime-observed maps retain
  the same structural and currentness meaning until successful execution
  replaces them.
- `outputs` maps every recipe output identity to its execution-time
  fingerprint.

A commit-pinned `git-repository` input remains a `data.json` origin. Its recipe
input is still the data name, and its observed value uses the inherited exact
repository-and-commit fingerprint form. Reproduction resolves and verifies the
recorded commit; it must not substitute the current checkout or a branch tip.

For a confirmed execution whose `requires_reproduction` is false, the recipe
and observed input/output key sets agree exactly and `script` is present. A
record requiring reproduction may contain a subset of still-applicable input
and output observations and may set `script` to null; code observations require
a retained script observation. Missing historical observations are unavailable
history, never current evidence or a match. Every fingerprint uses the closed
forms owned by the mechanical validator specification. `data.json` remains the
sole owner of input paths, classifications, and identity selection;
`pyrun.json` owns the historical observations that reproduction compares.

The fixed file, execution, parameter, string, input, output, environment, and
code limits are defined in [Fixed Resource Bounds](#fixed-resource-bounds).
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
2. the script, direct inputs, and observed local Python code remain stable;
3. every declared output exists with the declared kind and can be observed
   completely; and
4. the new execution passes the production decoder, identity, and output-set
   checks, and its outputs do not overlap any other owner in the validated
   initial state.

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

Ordinary `pyrun` strictly loads the complete existing file once under the entry
lock and retains that validated object through execution and publication.
Publication validates the new execution without decoding unchanged records a
second time. Direct edits to `pyrun.json` during the locked command are
unsupported: publication does not reload, merge, or detect them. External read
boundaries and coordinated operations that construct complete state continue
to apply the complete production decoder and ownership checks.

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
changed or its comparison fails. The mutation preserves the recipe,
observations, policy, versions, and `last_run_at`; later work, result
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

Edit the recorded Markdown command first, then run:

```text
log command sync --path LOG --entry ENTRY --cid CID [--dry-run]
```

Sync reconciles every current parameter expansion in the selected CID. It
preserves unchanged records, retains only applicable observations when a recipe
changes, applies policy-only changes without changing reproduction state, and
creates observation-empty pending records for missing expansions. Stale
parameter identities require explicit repeatable `--retire EXECUTION_ID`
acknowledgements; sync reports the exact retry flags and refuses partial or
extra acknowledgement.

Supply missing simple declarations in the same transaction with repeatable
`--add-origin NAME=PATH` or `--add-generated NAME=PATH`. Use `--rename OLD=NEW`
and `--remove NAME` only when no evidence, cross-entry reference, or unselected
CID depends on the declaration. Specialized identities and coordinated
evidence-aware changes remain under `log data`.

The operation validates complete `data.json` and `pyrun.json` candidates and
publishes both atomically under the entry lock. It never samples script, input,
or output bytes. `--dry-run` performs the same semantic checks and returns both
complete unified diffs without writing registries, diagnostics, or caches.

### Execution-Metadata Schema

Entry-local execution state accepts only strict `research-log-pyrun/v6`.
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

The current data and evidence readers accept only `research-log-data/v5` and
`research-log-evidence/v4`. Their conversion from data/v3-v4 and evidence/v3 is
a one-time plan-owned operation using disposable tooling, removed before plan
completion. No data/evidence migration CLI, legacy decoder, compatibility
reader, or conversion tooling remains in the final runtime. Reusable docs and
tests cover only the current data/evidence contracts; historical conversion
fixtures, if needed, belong only to that disposable conversion work.

This cutover does not remove the separate execution-state compatibility
contract. Current `pyrun.json` requires `research-log-pyrun/v6`; mechanical
validation retains the read-only
[Legacy Output Records](research-log-mechanical-validator-spec.md#legacy-output-records)
path for `pyrun-outputs.json` when no current file exists. That reader grants no
execution or conversion authority. A current observation may not be copied
into retained execution state as proof of an old run, and no action
reconstructs historical locator or classification state.

There is no legacy output projection or targeted-refresh evaluator. Fresh
execution, reproduction, and promotion preserve the distinction between current
observation, retained execution baseline, and evidence presentation baseline.

## Discovery And Planning

### Public Target

The public launch form is:

```text
log reproduce --path LOG [--entry ENTRY] [--include-all] [--recheck] [--jobs N] [--execution-timeout-seconds SECONDS] [--dry-run [--summary]]
```

Omitting `--entry` selects exactly one complete log. Supplying `--entry`
selects exactly that stable entry. There is no single-command, multi-log,
all-log, or project-wide reproduction operation.

`--jobs` accepts a positive decimal integer and defaults to 1. It is the maximum
number of concurrently active executions in this run, not a promise that the
cap can be reached. Graph readiness, path conflicts, project-wide exclusive
coordination, and available work may reduce concurrency. The accepted value is
immutable; status, stop, resume, recovery, and publication cannot override it.

`--execution-timeout-seconds` accepts an integer from 1 through 604,800 and
defaults to 300. The accepted value is an immutable per-command wall-clock
runtime limit measured from child launch. Queue and scheduling wait time do not
consume it. Status and resume retain the accepted limit and do not accept an
override.

Log and entry targets retain their existing evidence and command selection.
Use `log repair-check --path LOG --entry ENTRY --cid CID --execution-id ID` for one
current repaired invocation. It is isolated and synchronous, does not create a
run, does not apply automatic-policy admission, cannot resume or publish, and
never changes execution metadata, validation, results, or promotion state.

### Admission Gate

Before accepting or previewing work, reproduction evaluates the current log
once under the ordinary log lock. Incomplete evaluation or an unresolved global
admission blocker rejects preparation. Published validation files are neither
read nor freshness tokens. Current findings are admitted per connected
same-entry command chain: Structure, Evidence, and failed Provenance findings
exclude only their affected batches. Independent batches remain eligible, and
ordinary dependency propagation prevents admitted downstream work from running
when it depends on an excluded upstream batch.

Provenance and Hygiene findings caused only by a pending reproduction
requirement do not block reproduction. Runnable recipes with that requirement
are deliberately eligible so reproduction can clear it.

The validation subsystem's persisted batch projection determines finding
membership and admission through validation-owned `none`, `chain`, `entry`,
and `log` effects with exact affected identities. Reproduction must not
reconstruct groups or classify raw check failures independently. `none` remains
reportable without affecting executable work; `chain` excludes one projected
chain; `entry` excludes runnable work in one physical entry; and `log` refuses
the complete plan. A selected execution output must map to exactly one
projected chain; an absent or ambiguous execution-to-chain mapping remains a
whole-run integrity failure. Summary provenance that depends on
`provenance.output.reproduction_required` remains a non-failing dependent check and
does not create an additional admission blocker. A summary-only unresolved
reference with no association to evidence, registered data, execution state,
or runnable material has effect `none`; its reporting does not block unrelated
execution.

Projected chain and entry-scoped unresolved-group `entry` values are exact
entry-document IDs. Reproduction resolves each through the canonical entry
document grammar to its owning physical stable entry before matching selected
executions or recording batch admission. Split documents such as `e001a` and
`e001b` therefore share the `e001` execution owner without changing their
published chain identities. A chain matches an execution only when one of its
authored commands directly produces the execution's complete output set;
input-only, registry, and general artifact presence do not establish
production. Missing, invalid, absent, or multiply matching scopes still fail
closed, and batch admission records the physical stable entry ID. Existing
same-ID documents retain the identical mapping.

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

By default, a valid `auto_reproduce: false` execution with
`requires_reproduction: false` is classified as reproduction not needed before
policy is considered; its retained outputs bound traversal, so upstream work
is not selected solely for that current command. Planning stops before every
remaining non-automatic execution. Its retained output may serve as a boundary
only when its current fingerprint and required provenance state are valid.
This boundary is planning metadata, not an artifact outcome.

`--include-all` includes automatic and non-automatic executions within the same
selected entry or log boundary and traverses their upstream closure. It does
not widen the target or bypass validation. Scope is immutable after run
acceptance. The CLI must not prompt to widen it.

### Selection Policy

Incremental selection is the default. A current execution with
`requires_reproduction: false` needs no execution and does not require a saved
reproduction result. Of the commands that still require reproduction,
non-automatic commands are skipped by policy unless `--include-all` is present.
Every evidence-relevant command also has a source-closure digest. An unchanged
prior terminal failure or block prevents an eligible command from being
retried, but remains visible as that prior failure or block rather than being
classified as reproduction not needed. A remaining command is
selected when it has no applicable terminal state, its source closure changed,
or selected upstream work can change an input it consumes. Selection propagates
through only that command's reachable downstream closure; unrelated commands
do not need reproduction.

The source closure covers the canonical execution record after omitting only
`requires_reproduction`, including the recipe and environment; current script,
participating-code, direct-input, dependency-output, retained-boundary, and
comparison-baseline fingerprints; dependency identities; per-output comparison
definition identities; and localized planning disposition, reason, and failure
dependencies. It therefore represents the complete current reason that the
saved terminal disposition remains applicable.

`--recheck` selects every runnable execution in the current evidence-relevant
closure under the chosen log or entry target and automatic-reproduction policy, including
executions for which reproduction is not otherwise needed. Commands that remain
locally blocked are projected as blocked rather than executed. Recheck preserves execution
grouping, dependency order, target boundaries, retained boundaries, and
artifact-level result identity. It does not bypass validation admission,
repair a graph failure, or make an otherwise ineligible case runnable.

All-execution inclusion and selection policy are independent. `--recheck`
alone stops at verified retained non-automatic boundaries. `--recheck
--include-all` also selects non-automatic executions. Neither flag implies the
other.

Recheck is a launch-time planning input. The emitted plan records each
command's exact source digest; `run`, `not_needed`, `unchanged`, or `blocked`
selection; and the prior `failed` or `blocked` disposition only for an
`unchanged` selection. Selection and prior disposition remain separate facts.
The plan is the durable authority for execution and resume. It requires no persistent
selection-policy field. Commands that consume an accepted run or only query
published state do not accept `--recheck`.

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
Its default incremental policy selects commands that still require
reproduction, except for unchanged cached failures and blocks, plus the
reachable downstream commands that those selections may affect. It must not
infer command selection from prior artifact matches, and it must not preserve
downstream terminal state when selected upstream work can invalidate it.

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
launch. By default, it emits one
deterministic `research-log-reproduction-plan/10` projection with exactly
`schema`, `summary`, `target`, `include_all`, `jobs`,
`execution_timeout_seconds`, `admission`, `commands`, `comparison_context`,
`cases`, `executions`, `boundaries`, and `failures`.

`--summary` is valid only with `--dry-run` and replaces the complete JSON
projection on standard output with a bounded human projection. For log and
entry targets, it reports the target, admission state, incremental-or-recheck and automatic-or-all selection,
concurrency cap, per-command runtime limit, artifact-case count, runnable
ordinary and exclusive execution counts, localized planning-failure count,
boundary count, scheduling-path-claim
completeness, and per-entry runnable and exclusive counts. The entry table is
limited to the first 20 stable entry IDs and reports the number omitted.
Complete JSON remains available without `--summary`. The summary is
presentation only; it applies the same complete planning and final source
recheck and does not alter the deterministic plan contract. Historical
result/11 execution rows are not dry-run summaries and supply neither plan nor
boundary semantics.

`target` follows the target grammar below. Cases are sorted by canonical log
entry order and artifact path. Each case has exactly `entry`, `artifact`, `cid`,
`execution_id`, `disposition`, and `reason`; `disposition` is `run`, `current`,
or `failed`, and `reason` is null only when no qualification is needed.

Executions are in deterministic run order and each has exactly `order`,
`entry`, `cid`, `execution_id`, `depends_on`, `outputs`, `auto_reproduce`, `exclusive`,
`read_paths`, `write_paths`, `run_path`, and `writable_paths`. The four path
claim fields are the immutable normalized scheduling projection; path arrays
are sorted and unique. Before a run ID exists, run-local claims use the
`<run>/...` portable prefix and project paths use `<project>/...`; acceptance
resolves them beneath the chosen canonical run and project roots without
changing their identity or creating dry-run state. `depends_on` and `outputs`
are sorted unique identity arrays. A dependency reference is the
fully qualified string `<entry>:<cid>:<execution_id>` because a parameter
identity may legitimately occur under more than one CID or entry;
`execution_id` itself remains exactly the ID recorded in that CID's
`pyrun.json` bucket. Boundaries are
sorted and each has
exactly `kind`, `entry`, `name`, `artifact`, and `fingerprint`; `kind` is
`origin`, `cross_entry`, `non_automatic`, or `outside_queue`. The last kind is
used when an in-scope plan treats a producer as a retained boundary. Fields
inapplicable to a boundary kind are null rather than omitted. Historical
result/11 execution targets are passive rows with exactly `kind`, `entry`,
`cid`, and `execution_id`; they have no current planning or boundary semantics. Failures
are sorted artifact projections with exactly
`entry`, `artifact`, `outcome`, `reason`, and `dependencies`.

`admission` records the fresh evaluation identity, rules version, operation
date, and each admitted or excluded chain decision. It explains acceptance; it
is not a live freshness token and names no published validation file. `commands`
is the immutable selection and accounting inventory. Each compound `{entry,
cid, execution_id}` record retains its recipe, policy, selection reason, working
directory, and accepted execution observations. `executions` references those
command records and owns topological order, dependency references, complete
output membership, and scheduling claims.

`comparison_context` freezes each comparison definition and separately
identified evidence-only current observations. It is not an authority-file
manifest. The plan retains no whole-log source snapshot or validation
result/projection path or digest.

Before an invocation launches, the runner compares its script, helpers, and
resolved inputs with its accepted observations. Before comparison it verifies
retained output-baseline bytes against the recorded observations, then uses the
frozen exact or evidence-scoped definition. Changed planned sources,
declarations, inputs, helpers, or comparison definitions require a new run.
There is no whole-log rescan, published-validation recheck, or snapshot
certification during execution, resume, or publication.

Preview uses the same locked preparation as launch and releases the lock before
returning. It writes no run, workspace, checkpoint, result, report, cache, or
preview token; lock infrastructure is its only permitted side effect. Launch
prepares independently and never accepts a preview as certification.

## Durable Reproduction Jobs

### Launch And Identity

A non-dry launch with one or more selected executions creates one durable
background job, persists its immutable accepted plan and initial run state,
then releases the preparation lock before it starts its
supervisor, emits its run ID, and returns immediately. The job is independent
of the invoking terminal and agent turn. There is no foreground mode.

A non-dry launch with no selected executions normally performs a no-op
reproduction reconciliation. It creates no run ID, run folder, worker,
reproduction-result write, or reproduction-report write. Like every non-dry
launch, it first evaluates and publishes current mechanical validation under
the normal locks; that validation result and `validation.md` are separate from
reproduction state. The sole exception after that validation publication is
explicitly launched empty-target whole-log recheck recovery of unsupported
generated reproduction results, as specified in
[Compatibility And Evolution](#compatibility-and-evolution): it acquires the
scope and publication locks and atomically replaces the reproduction result
and report, while still creating no run or worker. Standard output is the
standard per-log summary using the
current plan's command partition: commands for which reproduction is not
needed, policy exclusions, and any blocked commands. Succeeded and failed
are zero because no command ran. Current artifact state remains a separate
tree. The latest completed run may be identified as historical context, but
its command counts must not replace the current reconciliation. This terminal
summary is returned immediately even when localized artifact planning failures
are present.

A run ID is an opaque, lowercase, filesystem-safe unique token produced by the
CLI. It is immutable and names the durable state, output workspace, diagnostics,
and staging paths for the life of the run. It is not derived from Markdown or
an execution recipe.

The accepted target, execution/entry/log kind, all-execution inclusion policy, `jobs`
value, and per-command runtime limit are immutable.
Management commands use only the recorded scope:

```text
log reproduce status --path LOG --run-id RUN_ID [--json]
log reproduce stop --path LOG --run-id RUN_ID
log reproduce resume --path LOG --run-id RUN_ID
```

They must reject `--entry`, `--include-all`, `--jobs`, and
`--execution-timeout-seconds`.

### Durable State

Each accepted run directory contains `state.sqlite`, the durable job authority.
It stores one immutable accepted plan and mutable state, checkpoints,
comparison context, and publication-retry state in typed tables. It is never a
result-store projection or a JSON aggregate. The plan owns the target,
selection, settings, command inventory, dependencies, recipes, comparison
context, and admission; mutable job rows own only operational state. Result
clearing never rewrites this database, staged outputs, diagnostics, retained
baselines, or entry-root `pyrun.json` observations.

`target` is exactly `{kind: "log", entry: null}` or
`{kind: "entry", entry: ENTRY}`. Entry IDs use the stable entry grammar. The
accepted plan fixes this target, `include_all`, `jobs`, timeout, and command
membership. Resume cannot add commands or change any accepted setting; current
runs have no one-execution target.

The `runs` row owns immutable run metadata and paths. The normalized
`accepted_*` rows are the single accepted plan/10 authority: admission,
commands, recipes, materials, dependencies, outputs, claims, cases,
boundaries, failures, and comparison definitions. They are inserted once and
are not lifecycle history.

The single `run_state` row owns mutable run lifecycle and aggregate artifact
counts. Its `status` is null while active and otherwise `complete`, `stopped`,
or `failed`. Its `phase` is `accepted`, `planning`, `preflight`, `executing`,
`comparing`, `publishing`, `stopping`, or null; a terminal status requires a
null phase. Lifecycle timestamps and the latest execution and operational
diagnostics are columns of this row. A complete run has no operational
failure, even when artifact outcomes include failures.

Each accepted execution may own one `execution_checkpoints` row keyed by its
accepted command identity. That row records `active`, `succeeded`, `failed`, or
`stopped`, its current permit or exact released-permit tombstone, checkpoint
time, first start, finish, accumulated elapsed time, failure fields, diagnostic
paths, and scratch ownership. `checkpoint_outputs` stores its canonical output
fingerprints as child rows. A stopped checkpoint is the only resumable
execution state; failed and succeeded are terminal within the run. Timing
begins at the first supervised child launch and accumulates only active
supervised runtime. Stop preserves the first start, leaves finish null, and
adds the next active interval on resume.

`run_owner` stores the one supervisor lease. `workers` stores bounded worker
rows with worker and parent identity, optional accepted execution identity,
PID, `running` or `exited` state, registration time, and latest observation.
Only running rows are live; exited rows are retained but hold no permit.
`staged_executions`, staged diagnostic rows, artifact comparison and evidence
rows, `execution_effects`, and `publication_state` separately own comparison,
external `pyrun.json` mutation, and publication-retry progress. No table stores
a whole-plan, checkpoint-history, active-execution, or worker JSON array.

The public status/7 object is a bounded projection of those rows, not another
durable record. It orders active executions by accepted plan order and derives
them from active checkpoints with attached permits. It derives active and
surviving workers from running worker rows, execution timings from checkpoint
rows, `total_executions` from accepted execution rows, aggregate outcomes and
diagnostics from `run_state`, and resumability from stopped lifecycle or the
exact failed-publication retry state. Its `timestamps` fields are null when the
corresponding lifecycle event has not occurred.

The database therefore durably retains the run identity, accepted plan,
lifecycle, progress, supervisor and worker ownership, execution checkpoints
and outputs, comparisons, requirement-effect checkpoints, and
publication-retry state.
Checkpoint transactions distinguish `succeeded`, `failed`, and `stopped` work
from an active execution after process or host failure. Cardinality and byte
limits are defined in [Fixed Resource Bounds](#fixed-resource-bounds) and do
not weaken this state contract. There are no current-format plan, run,
checkpoint, scratch-owner, supervisor, or staging JSON records.

Every durable-state read validates one bounded SQLite snapshot and its typed
rows. A reader must not combine rows from different snapshots. A canonical
historical JSON directory without `state.sqlite`, or a `state.sqlite` with an
unsupported `user_version`, fails as `reproduction.run.unsupported`. Malformed
SQLite or a malformed selected projection fails as `reproduction.run.invalid`;
cross-row corruption fails as `reproduction.run.invariant`. Missing, busy, or
unsafe current stores retain their specific `reproduction.run.missing`,
`reproduction.run.busy`, or `reproduction.run.path_unsafe` result. Writers use
short transactions while holding the run-state lock; callbacks retain the
already accepted immutable run ID rather than rediscovering it from mutable
state.

### Status

Default status is concise human text. `--json` emits one deterministic
`research-log-reproduction-status/7` object containing exactly `schema`,
`run_id`, `summary`, `target`, `include_all`, `jobs`,
`execution_timeout_seconds`, `status`, `phase`,
`active_executions`, `active_workers`, `execution_timings`, `completed_executions`, `total_executions`,
`artifact_outcomes`, `timestamps`, `latest_execution_diagnostic`,
`operational_failure`, `surviving_workers`, and `resumable`. The values are the
strict projection of run-local `state.sqlite` rows.
`resumable` is true only for a stopped run or the explicit publication-retry
case; it is not attempt lineage or a selector for replanning.
`active_executions` uses the run-state execution-reference shape and order.
`active_workers` contains every current worker record associated with those
references and is empty when no execution is active.
`execution_timings` contains only launched executions, in stable plan order,
with exactly `entry`, `cid`, `execution_id`, `state`, `started_at`, `finished_at`,
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

Callback, checkpoint, worker-history, and run-state persistence errors are
control-plane failures, not research-command outcomes. Such a failure stops and
reconciles the affected worker tree, preserves terminal operational intent, and
ends the run as `failed` after cleanup. If a child was launched, that launch
consumes its attempt in the run even when the control plane cannot publish a
research result; same-run resume must not invoke it again. Genuine child exits,
capture failures, output materialization failures, comparisons, and dependency
skips remain execution or artifact outcomes.

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
records exact survivor worker rows in `state.sqlite`, and keeps the scope
durably excluded even if the failed supervisor releases its inherited lock
descriptors. The stop request returns nonzero. Repeating `stop` retries the
bounded cleanup.

### Resume

`resume` is available only for a stopped run or the explicit publication-retry
case. It reacquires reproduction reservation ownership, reloads exactly the
accepted plan and validated checkpoint/staging inventory, then rechecks state
under run-state serialization. It preserves completed successful and failed
outcomes, comparisons, dependency skips, elapsed time, workspace, and run ID.
It launches only never-started work and work stopped without a durable terminal
outcome, after cleaning that invocation's incomplete outputs and scratch. It
does not replan, reread current source to adopt repairs, rerun failed work, or
accept options. `--recheck` applies only to initial launch.

For a current-format publication failure, resume reuses every durable
comparison, terminal failed attempt, dependency skip, and succeeded checkpoint
and performs no second research-command attempt. Older jobs are unsupported
and require a new current-format run.

### One-Attempt Rule

Within one immutable run, each compound `(entry, cid, execution_id)` launches at
most once after it has a durable terminal outcome. Multiple artifact cases and
dependent branches reuse that result. A failed execution remains failed, its
dependents are skipped with `dependency_failed`, and independent executions
continue. Resume may restart only interrupted work without a durable terminal
outcome; it never creates a continuation plan or accepted-attempt history.

### Recovery

Host or supervisor recovery performs reconciliation and worker cleanup only. It
must never restart research execution automatically. Every formerly active run
is reconciled, surviving registered workers receive the same bounded cleanup,
and its project-scheduler waiters and permits are reconciled under the scheduler
mutex. The run becomes reason-coded `stopped` only after no worker or permit
remains, except that a `stopping` run with non-null `operational_failure`
preserves that durable intent and becomes `failed` after cleanup. The scope
remains excluded by either the live descriptor lock or the current SQLite
owner/worker lifecycle until quiescent reconciliation is durable. Execution
continues only after explicit `resume` passes ordinary guards.

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
project-local Python and recorded environment. Each attempt receives distinct
runtime cache and diagnostic roots. `MPLCONFIGDIR`, `XDG_CACHE_HOME`, and
`MATLAB_PREFDIR` remain under its runtime root.

Direct execution and reproduction use the same bounded byte-copy, independent
destination-failure, pipe-drain, and source-close mechanics. Reproduction keeps
process-tree supervision, scheduling, deadlines, cancellation, and failure
precedence outside that shared component. Its retained stdout/stderr
diagnostics and declared captures are required destinations: a write, durable
flush, or bounded-drain failure stops the supervised tree and records
`capture_failed` unless incomplete worker cleanup has precedence. The isolated
repair check uses this same reproduction execution path without durable-job
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
to each launch and adds no shared scheduling claim. Regenerated dependencies become read-only to consumers after their
producer checkpoint is durable. The supervisor performs atomic materialization
into shared dependency locations. Independent executions in the same entry may
run concurrently when their logical output, input, and writable claims do not
conflict; a resumed stopped attempt reuses its original run path and receives
new scratch. Retained scripts, participating code, inputs, boundaries,
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
path used by stop. It writes a failed checkpoint with reason
`execution_timeout` and a message naming the exceeded limit. The timeout is an
execution outcome: dependants are skipped with `dependency_failed`, independent
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

The current reason vocabulary is `baseline_changed`, `baseline_unavailable`,
`boundary_changed`, `boundary_unavailable`, `capture_failed`,
`comparator_error`, `content_changed`, `cross_log_generated_input`,
`dependency_cycle`, `dependency_failed`, `direct_input_changed`,
`direct_input_unavailable`, `evidence_comparison_failed`,
`execution_exception`, `execution_failed`, `execution_timeout`,
`generation_failed`, `graph_limit`, `missing_input`, `missing_producer`,
`multiple_producers`, `output_materialization_failed`, `output_missing`,
`outside_entry`, `outside_queue`, `participating_code_changed`,
`participating_code_unavailable`, `reproduction.run.invalid`, `resource_limit`,
`safety_failure`, `script_changed`, `script_unavailable`, `non_automatic`, `stop_requested`,
`unsupported_format`, `validation_blocked`, `worker_cleanup_incomplete`, and
`worker_survived`.

### Authoritative Result

`<log>/.cache/results.sqlite` is the sole disposable local authority for
queryable reproduction results. The reproduction domain stores normalized
artifact, execution, command, and terminal-run projection rows keyed by their
stable identities. It has no maintained aggregate JSON encoding.

In consolidated store schema v15, each retained run has a positive internal
`run_pk`; the public `run_id` remains the only run identity exposed by reports,
queries, exports, or producing-run fields. Run-command and run-execution
junctions use `WITHOUT ROWID` composite primary keys. A historical command row
stores its queryable scalar fields, including its stable CID, once, plus one
bounded `details_json` list and one bounded `recipe_json` object. Historical
execution rows and command-to-execution relationships are CID-qualified. There
is no `detail_json` copy of those same values, and the unchanged public
command-detail object is reconstructed only for a selected row or explicit
export.

Cumulative publication and explicit export enforce the 64 MiB domain ceiling
with a canonical incremental encoder over normalized rows. The encoder stops
at the first over-limit chunk before constructing the aggregate result object;
publication rolls back the whole selected-key merge on failure. Ordinary
summary, artifact, command, and history queries continue to decode only their
selected bounded projection.

`summary` is the maintained summary path. `updated_at` is the latest successful
result publication time. `artifacts` is sorted by
canonical log entry order, then artifact path. The pair `(entry, artifact)` is
unique. `runs` is sorted by descending accepted time, then run ID, and has one
record per retained or availability-unknown run.

`commands` is sorted by canonical log entry order, CID, then execution ID. The
triple `(entry, cid, execution_id)` is unique. Each record stores the most recently
published terminal `succeeded`, `failed`, or `blocked` disposition, the exact
source-closure digest to which it applies, publication time, and publishing run
ID. A changed artifact still belongs to a `succeeded` command because command
completion and artifact matching are separate facts.

Every artifact record has exactly `entry`, `artifact`, `cid`, `execution_id`,
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

Every run item has exactly the fields shown. `command_outcomes` reconciles
every command execution unit in the selected log or entry into seven
mutually exclusive categories:

- `reproduction_not_needed` is a command whose current `pyrun.json` state says
  it needs no reproduction;
- `unchanged_failed` is a command that still requires reproduction but was not
  retried because its unchanged source closure retains a prior failure;
- `unchanged_blocked` is a command that still requires reproduction but was not
  retried because its unchanged source closure retains a prior block;
- `not_automatic` is a remaining command omitted by the default automatic
  policy;
- `succeeded` is an attempted command that reached its complete mechanical
  endpoint, regardless of whether its artifacts matched;
- `failed` is an attempted command that did not reach that endpoint; and
- `blocked` is a command selected for the current reconciliation but not
  attempted because of a localized planning blocker or another selected
  command's failure.

`total` is exactly the sum of those seven values. Each target command is
counted once even when it produces several artifacts. New publications always
record the complete mapping. `command_records` is the immutable, canonically
ordered historical query projection for those same commands. It retains the
accepted recipe, working directory, policy and exclusivity flags, queue and
requirement state, accepted selection and prior disposition, source digest,
planning detail, accounting bucket and reason, and terminal disposition. Its
bucket totals must exactly equal `command_outcomes`. Later command metadata or
terminal publications never reinterpret these records.

One run ID owns one accepted plan and each selected execution owns at most one
terminal outcome. Resume completes only never-started or stopped work in that
fixed plan, or retries its publication; it never merges a later attempt under
the same run ID. Artifact mismatch does not keep the command queue unresolved:
a command that ran to completion is a durable execution success regardless of
comparison outcome.

The `executions` array records one explicit timing projection for each launched
execution in accepted execution order. Planned work that never launched has no
timing item. Timing is
diagnostic only: it does not affect identity, currentness, selection,
comparison, or reproduction-requirement update. Its target follows the run-state
target grammar. `status` is `complete`, `stopped`, or `failed`; an active run is
read through status and is added to the published index only when a lifecycle
event safely publishes it. `finished_at` is null for a resumable stopped run.
Artifact outcome counts use all five required keys. `folder.path` is the
normalized project-relative run directory; `availability` is `available` or
`unknown`.
A conclusively absent directory causes the whole run item to be removed rather
than persisting an `absent` value. Current artifact records retain their run ID
after that historical run item is removed; run-directory retention is not a
precondition for retaining the authoritative artifact outcome.

Unknown fields, duplicate artifact or command pairs, duplicate run IDs, invalid
ordering, or inconsistent counts fail decoding. The cardinality and byte limits in
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
reconciles the complete selected log closure. The same publication replaces
newly terminal command records, preserves unchanged command records, and prunes
records no longer reachable from current execution state.

A stopped run or an operational failure before final reproduction publication
leaves the current artifact map unchanged. Confirmations already written for
matched executions remain intact. A publication failure may be retried from
the durable comparison and publication rows through the guarded resume route without
rerunning terminal execution attempts. Terminal lifecycle events may still
update the run index and human Runs table without publishing partial artifact
outcomes.

### Currentness

Every artifact and command result records `recorded_at`, the commit time of that result to
the reproduction domain, regardless of outcome. A result is implicitly
stale when the producing execution has a non-null `last_run_at` later than
`recorded_at`. Recipe, script, code, input, validation, and dependency changes
may also make a case ineligible or require new work under the graph contract.

Incremental command currentness is exact digest equality between the saved
command record and the newly planned source closure. Artifact result
currentness remains a reporting concern; it is not used to infer reusable
command state.

Currentness is derived when planning, querying, or rendering. Ordinary
`pyrun` never reads reproduction results. Neither file is rewritten merely to
mark a result stale, and v1 has no currentness cache.

Results no longer reachable from current `evidence.json` are ignored immediately
and contribute to no entry or log coverage. A later reproduction
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

The directory contains run-local `state.sqlite`, one project-layout
`workspace/`, private runtime and diagnostic directories, and the retained
execution output trees. Identity-scoped comparison rows in `state.sqlite` are
the durable staging index. Each comparison retains byte count, completion,
diagnostic paths, entry/CID/execution identity, workspace path, and the closed
artifact set. Each artifact row records its declared kind, availability,
exact staged path, outcome and reason, comparison profile, retained and
regenerated fingerprints, and any evidence-scoped detail. The comparison and
artifact rows commit atomically before the separate `pyrun.json` requirement
effect is attempted.

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
partial or stale set cannot be promoted.

Promotion copies; it never moves or modifies staged source files. Other
executions in the same run directory remain independently available. Missing
manually deleted staging material fails inspection or promotion clearly but
does not invalidate an already published reproduction result.

Promotion is a researcher-directed research mutation. It atomically updates
retained outputs, the related `pyrun.json`, and reproduction state. It must not
change `data.json` declarations, evidence records or their artifact baselines,
or run validation. It leaves the retained run-local staged sources intact.

## Locking And Publication

### Scope Locks

Reproduction uses the existing lock implementation beneath
`<log>/.cache/research-log-operations/`. Reproduction-only reservations are
separate from ordinary source-operation locks: a log target holds exclusive
`reproduction-log.lock`; an entry target holds shared `reproduction-log.lock`
and exclusive `reproduction-entry-ENTRY.lock`. They exclude overlapping
reproduction targets while allowing distinct-entry runs. A reservation is held
from accepted launch or resume through worker and permit cleanup; normal log
and entry locks are never held while research commands execute.

Before acceptance, a serialized active-target check rejects overlap:

- an entry conflicts with the same entry and its enclosing log;
- a log conflicts with itself and every entry in that log; and
- distinct entries in one log may run concurrently.

Reproduction reservations coordinate only overlapping reproduction targets.
They do not hold ordinary source-editing or `pyrun` publication locks for a
run's lifetime. Those operations retain their existing short operation locks;
source edits after acceptance are unsupported and require a new run when an
invocation-scoped observation detects them.

### Project-Wide Scheduling

Project-wide ordinary and exclusive permits use the existing operation-lock
implementation at the current Git project root, alongside rather than replacing
entry and log scope locks. A brief project scheduler mutex protects one bounded
SQLite coordinator beneath the project operation-state directory. Its normalized
tables contain the ticket counter, waiting exclusive tickets, active permits,
and their ordered path claims. This is coordination state, not research state
or a reproduction result.

The coordinator path is
`<project>/.cache/research-log-operations/reproduction-scheduler.sqlite`; its
mutex is `reproduction-scheduler.lock` in the same operation-state directory.
It uses SQLite `user_version=2`. `scheduler_state` owns the nonnegative,
monotonically increasing next ticket. `scheduler_waiters` owns the ticket,
run/entry/CID/execution identity, accepted plan order, supervisor PID, and
registration time. `scheduler_permits` owns the permit ID, kind, same accepted
identity and order, supervisor PID, accepted run path, and grant time.
`scheduler_claims` owns each ordered `read`, `write`, or `writable` absolute
normalized path. An exclusive permit is the only active permit. Empty tables
remain valid coordinator state; neither the database nor mutex is execution or
research authority.

An ordinary permit is admitted only when it conflicts with no active permit and
no exclusive ticket is waiting. An exclusive execution first records its ticket,
which closes ordinary admission, then waits without holding the scheduler mutex.
After active permits drain, the first `(ticket, run_id, plan_order)` tuple
becomes the sole active exclusive permit. A later ticket cannot overtake it. Scheduling never reserves
unrelated host processes or coordinates projects that do not share the current
Git root.

The lock order is reproduction reservation, normal log/entry operation lock,
promotion-index mutex, scheduler mutex, run-state lock, entry-local
reproduction-requirement lock, log publication mutex. No code may acquire an
earlier lock while holding a later one. The scheduler mutex is never held while waiting for
capacity, running or stopping workers, comparing artifacts, publishing results,
or invoking validation. A transition that touches coordinator and run state
takes scheduler then run locks. Permit admission validates the exact accepted
execution, plan order, kind, resolved claims, and durable running supervisor
before creating a scheduler row. It commits the grant before attaching the
same permit ID to the checkpoint. A crash between those commits is reconciled
from the exact accepted identity and live owner; a retry after attachment is
idempotent. Release deletes the scheduler permit before clearing the exact
checkpoint permit. The checkpoint retains the released permit ID as a tombstone
so only an exact retry is idempotent after interruption.

A scheduling permit is released only after the attempt's `succeeded`, `failed`,
or `stopped` checkpoint is durable and every registered worker is gone. An
incomplete stop or recovery retains the active permit and a current SQLite
owner/worker exclusion while a worker survives; the failed supervisor may
release its inherited descriptor only after that durable exclusion exists.
A stopped waiter removes its ticket before releasing its scope lock. Recovery
reconciles coordinator entries against strict run state and live supervised
process identity; it may remove a proved-dead waiter or permit but never infer a
completed attempt or launch work. Coordinator corruption or unavailable process
inspection is an operational refusal, not permission to bypass exclusion.
Permit admission also reconciles an entry whose supervisor is dead when its
canonical owner run is already terminal, records no active execution, and records
no surviving worker. If those conditions cannot be proved, admission fails with
`reproduction.scheduler.reconciliation_required` instead of waiting indefinitely;
the owner run must be inspected or recovered before retrying.

Historical reproduction jobs are never rewritten, migrated, deleted, or
decoded. A canonical historical JSON run directory without `state.sqlite` is
recognized only by its path. Status, stop, resume, recovery, promotion, and
publication reject it with
`reproduction.run.unsupported` and direct the caller to start a new current
run. It creates no new file and never blocks current-format admission or
scheduling because it owns no scheduler rows.

### Shared Publication

Concurrent distinct-entry runs share the reproduction domain of
`.cache/results.sqlite` and `reproduction.md`. Their shared writes must use one
brief log-local publication mutex built on the existing lock infrastructure.
It is not a reproduction scope lock and is not held during planning, execution,
comparison, or per-execution reproduction-requirement update.

Publication holds the run-state lock, then this publication mutex, then the
result-store lock. It reloads current shared state, verifies retained accepted
invocation and comparison evidence, and commits the selected result keys as one
result-store transaction. Before releasing the locks it records the returned
generation in `state.sqlite`; report composition, file replacement, and the
checked report marker follow without repeating that result transaction.

A failure before the result transaction commits returns the durable publication
stage to `ready`. A failure after it commits retains `result_committed` and is
report-only on explicit resume. If the process dies before recording the
generation, the unique run ID and immutable run metadata distinguish `absent`,
`exact`, and `conflict`. An exact match records the current reproduction
generation and materializes the current aggregate; this may be a later
generation committed by another completed run. A conflict fails closed. The
publisher never reads or writes validation state.

### Reproduction Requirement And Post-Reproduction Validation

When a command reaches its complete mechanical endpoint, reproduction atomically changes only that execution's
`requires_reproduction` field to false in its entry-local `pyrun.json`. This is
independent of artifact comparison:
matched, changed, and comparison-failed outputs all belong to a completed
command. The update takes the owning short entry guard; active-run reservations
and promotion conflicts remain checked independently. Each update is
independent and durable; no later execution or publication outcome restores
the requirement.

After reproduction-result publication succeeds and the run becomes complete,
the supervisor releases its reproduction scope lock without invoking validation.
Validation owns its domain in `.cache/results.sqlite` and `validation.md` only when explicitly
requested. Reproduction performs neither targeted refresh nor full validation.
A later validation outcome does not change the completed reproduction status,
reproduction requirements, or reproduction results.

### Promotion Conflicts

Promotion acquires the producing entry's normal operation lock. It is rejected
while that entry or enclosing log is under reproduction and whenever an active
accepted plan records a promoted artifact as an input or retained comparison
baseline. While active, promotion publishes its complete output set in operation
state so a newly prepared reproduction with an intersecting accepted input or
comparison baseline is likewise rejected.
Its shared-state changes use the publication mutex.

## Human And Agent Interfaces

### Generated Files And Cutover

Reproduction owns:

```text
<log>/.cache/results.sqlite
<log>/reproduction.md
```

Validation continues to own its result-store domain and `validation.md`.
Cutover removes the legacy Reproduction result section from `validation.md`.
Validation may link to `reproduction.md` but must not duplicate reproduction
state.

The machine JSON paths are ignored cache state. The former
`reproduction/results.json`, `validation/results.json`, and
`validation/batches.json` locations are removed during the path cutover and are
never read as fallbacks. A missing reproduction result means a cold cache; a
new reproduction rebuilds current outcomes instead of reconstructing them from
Markdown.

Every maintained summary receives:

```markdown
Reproduction: [latest report](<log>/reproduction.md)
```

Scaffold creates neither an empty result store nor a placeholder report. The
first completed reproduction creates the reproduction domain and derives the
report. Removing result data never touches a run-local `state.sqlite`, retained
baselines, or `pyrun.json`; surviving reports are nonauthoritative and cannot
rebuild results. Because current successful commands remain cache-independent,
rebuilding the machine result requires an
explicit `--recheck` reproduction.

### Human Report

`reproduction.md` is deterministic, generated, source-controlled,
nonauthoritative human output.
No researcher or agent edits it. One centralized compositor produces both the
file and the complete ready-to-present output of:

```text
log reproduce report --path LOG [--entry ENTRY]
```

The same CLI owns compact per-log and cross-log projections:

```text
log reproduce report --path LOG --summary [--format json]
log reproduce report --root PROJECT --summary [--format json]
```

The per-log JSON uses `research-log-reproduction-summary/5` and includes
nullable `resolved` beside the latest completed run ID. The cross-log JSON uses
`research-log-reproduction-root-summary/5`; its coverage object reports
resolved and unresolved completed logs separately. Human summaries name an
unresolved latest run as resumable, and the generated Runs table distinguishes
`complete (resolved)` from `complete (unresolved)` without changing the stored
run status.

The complete per-log CLI report must use the same counts, vocabulary,
ordering, and wording as the file projection. A reproduction agent presents compact
output unchanged by default and does not parse generated files or reconstruct
a summary. It requests the complete per-log report only when the researcher
asks for artifact or run detail.

The removed single-execution presentation route previously accepted a run ID.
Aggregate report routes operate only on log or entry state; they do not inspect
run IDs. Ordinary `status --run-id` remains the current lifecycle inspection
route for an accepted aggregate run. Historical result/11 rows may retain prior
one-command observations for read-only rendering, but they are not current
reports or lifecycle state.

The compact per-log projection has two visibly separate trees. The command tree
starts with every command in the target, separates commands whose reproduction
was not retried, commands skipped by policy, and commands selected for
execution. The reproduction-not-retried count combines commands that need no
reproduction with unchanged prior failures and blocks retained without another
attempt; it does not expose those prior dispositions. The tree nests
`Succeeded`, `Failed`, and `Blocked` below the selected count. The artifact
tree starts with every current reachable artifact, separates `Matched`, `Not
matched`, and `Not compared`, then nests the reasons for non-comparison. A current
`matched` result contributes to `Matched`, a current `changed` result contributes
to `Not matched`, and failed, comparison-failed, or skipped results
contribute to `Not compared`. These three artifact categories are mutually
exclusive and sum exactly to artifact `Total`.

Non-comparison reasons are listed as `comparison failed`, `command failed`,
`command blocked`, and `command skipped`. Zero-count reasons are omitted, and
`command skipped` is always the last visible reason.
An artifact skipped because a dependency failed is blocked; policy- or
scope-skipped artifacts remain skipped. A pre-execution planning failure is
also reported as command blocked, using the command's terminal disposition;
it is not reported as command failed merely because the artifact outcome is
`failed`.

The projection explicitly states that command and artifact totals are different
units and need not match because one command may produce several artifacts.
It also states that a succeeded command ran to completion and that matching is
reported separately at the artifact level.
An older result without command accounting explicitly asks for a new
reproduction run; the report does not reconstruct historical counts. The
cross-log projection has one row per canonically discovered maintained summary,
two compact tables, separate
accounted totals, and explicit counts of complete, not-yet-reproduced, and
unavailable logs. Its JSON form uses the schemas listed in
[Versioned Surfaces](#versioned-surfaces).

The complete report header contains the latest completed run and the same two
summary trees. It omits the report-generation timestamp. It has no aggregate pass/fail headline.

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

### Bounded Command Queries

Agents inspect the command units behind compact command counts through:

```text
log reproduce commands list --path LOG [--bucket BUCKET] [--entry ENTRY] [--reason REASON] [--run-id RUN_ID] [--format text|json]
log reproduce commands show --path LOG --entry ENTRY --cid CID --execution-id EXECUTION_ID [--run-id RUN_ID] [--format text|json]
```

Without `--run-id`, both commands select the latest completed run. `list`
returns at most 50 deterministic records and always reports exact matched,
returned, and omitted counts. Its public buckets are
`reproduction-not-retried`, `skipped-by-policy`, `succeeded`, `failed`, and
`blocked`; entry, bucket, and exact reason filters are combinable. `show`
returns one exact entry/CID-qualified execution, including its recorded recipe,
working directory, automatic-reproduction policy, run selection, accounting
reason, declared inputs and outputs, and any available planning detail. The
list includes an `error` object for failed and unchanged-failed commands:
`type`, a single-line `message` limited to 512 characters, `source`
(`stderr`, `stdout`, `checkpoint`, or `unavailable`), and `truncated`.
Other rows have `error: null`. Recognized exception and argument-error lines
come from bounded retained log tails; otherwise the checkpoint failure is
used. Missing diagnostics are explicit. These summaries describe recorded
errors, not inferred root causes. The retained run is loaded once per listing.
The text list displays the error type and message and prints a shell-safe
`commands show` invocation for every returned
row, preserving the caller's executable and log-path spelling.

These queries use the same seven-category accounting projection that produced
the selected run's compact counts. They read that run's immutable
`command_records` and reconcile every projected row against its published
totals before returning it. For a launched command, `show` also resolves the
selected run's exact retained directory and newest terminal checkpoint for the
compound entry/CID/execution identity. Command detail schema
`research-log-reproduction-command/3` includes the checkpoint failure,
timing, and observed outputs plus retained stdout and stderr projections. Each
stream projection records its project-relative path, availability, byte count,
whether it was truncated, and at most the final 16 KiB with terminal control
characters sanitized. A missing, removed, invalid, or unavailable run
directory or stream is reported explicitly without hiding the immutable
command record. The query never consults current `pyrun.json`, reinterprets
historical policy, or requires another reproduction because a command was
changed, removed, or reclassified after publication.

The reproduction-result contract centrally decides whether a run has current
command-query metadata. Command queries do not provide a partial compatibility
path when it does not. Both `list` and `show` fail with
`reproduction.command.schema_unsupported` and instruct the caller to run
reproduction with `--recheck` to rebuild the generated result. They do not
reconstruct command rows from an accepted run directory, terminal command
records, aggregate counts, or current command metadata. Malformed records in a
current command-query projection are invalid rather than outdated.

Both commands are bounded read-only queries. They never validate, reproduce,
repair, publish, clean up, or write a file. Run-level lifecycle diagnosis
remains on `status --json`; per-command retained diagnostics belong to
`commands show`.

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

Parallel scheduling uses `research-log-pyrun/v6`. A current reproduction job
uses run-local SQLite `user_version=2`, one accepted
`research-log-reproduction-plan/10`, and the public
`research-log-reproduction-status/7` projection. JSON
`research-log-reproduction-run/7` and every earlier accepted reproduction job
format are unsupported immutable history: no current consumer decodes them
with defaults, resumes them, or transfers their execution provenance. Start a
new SQLite-backed run instead. The maintained-corpus execution-state cutover
is complete.

The result reader accepts only the current consolidated result-store schema.
Missing, malformed, busy, or unsupported stores fail precisely and do not fall
back to old JSON, reports, or run directories. A writer creates an absent store
but never overwrites malformed or unsupported state. A later reproduction may
publish fresh current results only through its normal accepted-plan path.

An explicitly launched whole-log `--recheck` also recovers unsupported results
when the accepted target is completely empty: no recorded commands (including
nonautomatic commands), artifact cases, boundaries, or planning failures. Under
the whole-log scope and publication locks, it uses the accepted no-work plan and
atomically replaces only the generated result and human
report with canonical empty, not-yet-reproduced state. It creates no run,
claims no successful execution, and invokes no research execution or worker;
the enclosing non-dry launch has already evaluated and published current
mechanical validation before reaching this recovery. Supported history and
absent results remain unchanged. A dry-run preview never performs this
recovery; incremental, partial, policy-skipped, and blocked no-work targets do
not gain unsupported-state replacement authority.

Mechanical validation uses direct execution association and an output-owner
index for current execution state. It has no legacy output projection or
targeted-refresh adapter. This does not authorize legacy declaration or
execution-state decoding, recording, reproduction, or baseline transfer.

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

Command-oriented version 6 execution state, parallel planning and scheduling,
safety, run-local execution, exact and evidence-scoped artifact comparison,
durable comparison records, immediate requirement clearing, independent result
publication, current projection, bounded read-only queries, durable job
control, immutable completed-run command inspection, fixed-plan stop/resume,
publication retry, lost-supervisor reconciliation, explicit
post-reproduction validation, and whole-execution copy-based promotion are
implemented. The maintained-corpus exclusivity cutover is complete, and earlier
execution-state schemas are rejected.
Promotion has no validation refresh; reproduction has no targeted-validation
path. Maintained-corpus initialization and the bounded
entry-level cutover evaluation are complete. Full maintained-corpus
reproduction remains gated by the reproduction plan. The frozen result and
status fixtures remain the compatibility boundary.

## Part 3.C Current Repair Boundary

The current isolated repair operation is `log repair-check --path LOG --entry
ENTRY --cid CID --execution-id ID`. It uses current declarations and retained output
baselines in an isolated synchronous workspace. It never creates a run or
changes generated results, reports, validation, promotion, or execution
metadata. Bare reproduction targets are only log or entry. Current accepted
plans are plan/10, their mutable lifecycle lives in run-local SQLite
`user_version=2`, and status/7 is a derived public projection. State rows bind
each planned command and execution to its stable CID. JSON run/7 is
unsupported historical job state, not a current mutable record. Earlier plans
are rejected without migration. Result/11 retains passive read-only rendering
of historical one-command rows.

`repair-check` requires one exact stable entry, one stable CID, and one complete
lowercase `pyrun-exec/v2:<64 hexadecimal digits>` identity. It resolves exactly one
current Markdown invocation whose recorded recipe remains identical. Unknown,
malformed, absent, ambiguous, or changed-recipe selections fail before a
workspace exists. The operation has no dry run, planning, admission, automatic
policy, scheduling, resume, status, report, publication, validation, or
promotion lifecycle.

It accepts `--execution-timeout-seconds` from 1 through 604,800; the default is
300 seconds and applies to the isolated child wall-clock execution. It first
holds the reproduction reservation and then the ordinary log lock while loading
authority. It releases the log lock during execution, reacquires it to confirm
that authority and retained baselines are unchanged, and retains the
reservation for the complete synchronous call. A reservation conflict fails
before workspace creation.

The authority snapshot includes the current summary, entry declarations,
recorded execution, current command and source files, declared direct inputs,
evidence comparison definitions, and every retained output baseline. Current
inputs are reported beside their recorded observations; a historical input
difference is information, not an adoption. The operation consumes an
available current direct input even when it differs from the recorded
observation, and reports that difference. It refuses unavailable or unsafe
inputs, changed retained baselines, and any authority, source, input, baseline,
or comparison context that changes during the call. It never adopts a new
recipe, declaration, participating-code observation, input observation, or
evidence rule.

After preflight, the retained workspace is
`<project>/tmp/repair-check/YYYY-MM-DD/repair-check-<log>-<entry>-<random>/`.
Private outputs and stdout/stderr diagnostics remain there for inspection.
After workspace creation, it is retained for every terminal outcome, including
unavailable and cancelled calls. Selector, authority, input, baseline, and
isolation-preflight failures that occur before creation return a null workspace.
The operation confines child outputs to that workspace and must not modify
retained research inputs, baselines, `pyrun.json`, reproduction cache,
generated report, validation cache, requirement flags, or promotion state.

Its only machine result is one `research-log-repair-check-result/1` object.
It has exactly `schema`, `summary`, `entry`, `cid`, `execution_id`, `status`,
`exit_status`, `published:false`, nullable `workspace`, `execution`, `inputs`,
`outputs`, `diagnostics`, and `limitations`. `execution` reports return code,
checkpoint state, failure, recorded policy fields, and current source records;
`inputs` report recorded and current fingerprints plus historical difference;
`outputs` report isolated paths and comparison outcomes; diagnostics carry
stdout/stderr text and paths. `limitations` includes `selected_repair_only`.
No result has a run ID, plan, publication, or promotion field.

`matched` exits 0; `different` exits 1; `worker_cleanup_incomplete` exits 2;
and `execution_failed`, `comparison_unavailable`, and `unavailable` exit 3.
An interrupt or termination produces `cancelled` with exit 130 or 143 unless
worker cleanup is incomplete, which takes precedence. Timeout is an execution
failure. Cancellation stops the supervised process tree; cleanup retains
diagnostics and the workspace, and an incomplete cleanup is never masked as a
successful cancellation. Entry or full validation is the only clearance path.
