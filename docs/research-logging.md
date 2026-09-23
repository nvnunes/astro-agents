# Research Logging

This is human-facing researcher documentation for using the
`research-logging` skill. It explains what researchers can ask the skill to do,
what to expect from the workflow and visible research record, and which
research decisions remain theirs.

This document is not a specification for agent behavior, metadata grammar, or
validator implementation. Do not use it as a completeness checklist or proxy
for the `research-logging` skill. The skill is a separate, self-documenting
agent surface and does not depend on this guide. Repair alone may progressively
consult the relevant section of
`docs/research-log-mechanical-validator-spec.md` when malformed or legacy state
prevents an owning CLI action from operating. The mechanical-validation CLI
and its supporting tools must also adhere to that specification. The
[reproduction specification](research-log-reproduction-spec.md) separately owns
current execution state and mechanical reproduction. These surfaces must be
conceptually compatible, but have separate authority and do not repeat the same
detail.

## Workflow at a glance

A research log uses eight core operations:

1. **Record** research activity in a new or existing log, using numbered, dated
   entries with their supporting material.
2. **Replace** a named experimental section and its owned material when you no
   longer intend to retain the superseded work in the active log.
3. **Update Summary** when you want the current research state and follow-ups
   brought up to date.
4. **Review** the analysis, evidence, or research record under a
   researcher-selected semantic review group or focused lens.
5. **Validate** mechanically that presented computational results match their
   declared sources, have visible provenance, and leave no unexplained retained
   material.
6. **Reproduce** the evidence-relevant artifacts of one entry or log through a
   mechanical, JSON-authoritative background workflow.
7. **Repair** a named malformed, legacy, or invalid research-owned condition
   without broadening into unrelated corrections.
8. **Reorganize** explicitly selected structure without changing research
   meaning.

Reference management supports these operations when needed; it is not an
additional stage. Repair and Reorganize require explicit requests or approval;
neither is implied by Record, Review, or Validate. Reorganize preserves entry
IDs except during an explicitly requested simultaneous reorder. It preserves
evidence associations except for records explicitly selected for coordinated
transfer between documents or entries.

You own the scientific methods, interpretations, accepted findings, decisions,
and next steps. Agents may organize material, implement and run code, check
results, and draft text, but they do not invent evidence or turn a proposal
into an accepted conclusion. Reported computational results come from executed
code and saved source data, not from generative AI.

The following sections explain each part of the workflow. Validation is one
part of that workflow, not the organizing principle for the whole log.

## How a research log is organized

Each research log has a current Markdown summary and a directory with the same
base name. The directory contains numbered entry folders, entry documents,
supporting material, evidence records that connect results to sources, and
source-controlled validation and reproduction summaries. Disposable machine
state lives below `.cache/`. The summary describes the current state; entries
and their saved material preserve the detailed research record.

The minimum structure is:

```text
<log>.md
<log>/
  entries/
```

A generated report and disposable query results appear only after their owning
operation completes. A populated log may contain:

```text
<log>.md
<log>/
  refs.bib
  scripts/
  validation.md
  reproduction.md
  .cache/
    results.sqlite
  entries/
    2026-05-01-e001-calibration-drift-check/
      e001.md
      data.json
      evidence.json
      pyrun.json
      retention.json
      pyrun -> <installed launcher>
      data/
      images/
      scripts/
```

Reproduction runs additionally retain durable job state, checkpoints,
comparison context, and diagnostics beneath the project's `tmp/reproduction/`
run directory. Each current run uses one run-local `state.sqlite`; project-wide
ordinary/exclusive admission uses
`.cache/research-log-operations/reproduction-scheduler.sqlite`. Clearing
disposable result rows never changes that run-local state, authored evidence
baselines, fingerprint/selection caches, or `pyrun.json` observations. The
Markdown reports are derived from the result store and may be rerendered; they
are never machine authority.

Create other optional files and folders only when they are needed. Start navigation
from the summary for current understanding, scan `entries/` by date and topic,
and open entry documents for the detailed record.

## The research-log workflow

### Record

Use Record to carry out and preserve dated research in a new or existing log.
During an active investigation, this is one integrated loop: implement or
revise scripts, run the research, retain and analyze its outputs, document the
evidence, and draft observations grounded in those results. Recording updates
all directly affected material together, including prose, commands, saved
results, source links, citations, input indexes, and supporting evidence
metadata. It preserves the fixed validation and reproduction report links and
every generated report file. Check outputs as they are produced;
these checks are part of doing the research and do not establish validation
status.

Record the research, not the agent's activity. Do not turn an entry into an
agent diary or work log by narrating skill use, routine successful checks, file
housekeeping, or task progress. Include a check only when its method is part of
the research or its outcome affects the evidence, interpretation, reuse, or a
stated limitation.

When recording work completed elsewhere, preserve the scripts, commands,
settings, and artifacts that were actually used. Do not invent a cleaner
workflow, rewrite a historical command to match current conventions, or rerun
the work solely to document it. Identify missing material or limits on later
reconstruction plainly.

Record maintains entry navigation but leaves current understanding and
log-level follow-ups unchanged. It does not decide whether an update is needed.
Use Update Summary when you want them reconciled with the entries. A request
that includes both operations completes Record first and Update Summary second.

Keep explicit `Follow-up:` items in the entry during Record. Add or revise the
summary's `## Follow-ups` section only during Update Summary.

#### Work outside the log

Not every investigation needs to enter the research log immediately. Quick
calculations, scratch scripts, exploratory plots, and preliminary comparisons
may remain outside it. The existence of a research log does not make this work
part of Record. Until you ask to preserve it, the work carries no promise that
its commands, inputs, or outputs will remain available for reconstruction.

Work moves into Record when you ask to retain it, add it to an entry, cite or
present it as research-log evidence, or use it as the basis for a logged result
or decision. Preserve the actual scripts, commands, inputs, and outputs that
still exist. Do not replace them with a cleaner invented workflow or rerun the
work solely to create a better history. State missing material as a
reconstruction limit, and do not present an unsupported numerical result as
durable computational evidence.

Record only the material relevant to the retained evidence or decision, not
every exploratory dead end. A result does not enter the log merely because it
appears important; you decide whether to record it.

#### Start a research log

Start with the minimum structure and a summary containing only known context.
Create that structure through the research-logging skill's path-qualified
management entrypoint:

```text
<skill>/scripts/log init --path <log> --title <title>
```

If you already have work to record, follow successful initialization with
`log add` using the local start date, title, and descriptive slug. It allocates
`e001`, creates the minimal entry and its runner link, and adds the summary
item. Otherwise leave `entries/` empty. Do not create reference, script, data,
image, or evidence files until they are needed. The commands refuse an existing
or partial target rather than merging, overwriting, or completing it as a
retry.

#### Record a new investigation

Start a new entry for a distinct topic or later investigation. Use the local
start date and invoke:

```text
<skill>/scripts/log add --path <log> --date <YYYY-MM-DD> \
  --title <title> --slug <slug>
```

The command allocates one above the highest consistently observed entry ID,
never fills a gap, creates the minimal entry document and `pyrun` symlink, and
appends only the new `## Entries` item. It leaves summary interpretation,
follow-ups, report-navigation prose, and generated validation unchanged. Safe
allocation requires one unambiguous Entries inventory whose canonical links and
IDs agree with the physical entry directories and documents; unrelated summary
header wording and row order are not repair prerequisites.

#### Continue an investigation

Continue an existing entry when the new material extends the same topic or
comparison. If several entries could fit, choose the target entry explicitly.
Continuing does not automatically rename, split, merge, move, or remove
existing material. A new section within the chosen entry is ordinary Record
work, not reorganization.

If the work clearly belongs in a different entry, choose its destination before
recording it. Choosing another existing entry or starting a new one does not
authorize moving earlier material.

### Reorganize

Reorganize a log only when you request it or approve a recommendation from
Review. A recommendation does not change the log by itself. Rename, split,
merge, move, or remove material without changing stable entry IDs or research
meaning, except that an explicitly requested reorder assigns new sequential
entry IDs. The agent makes the semantic and Markdown changes; the tested
`log reorganize` commands apply only closed identity and coordinated registry
changes after verifying that work. Split an entry only when distinct topics
impair retrieval; length alone is not a reason. Split documents stay in the
same entry folder and use suffixes such as `e002a.md` and `e002b.md`.

Identity changes inspect only declarations and references whose coordinates may
change; they do not make unrelated research bytes a mutation prerequisite and
they preserve recorded execution observations. Cross-entry transfer verifies
selected material and moved presentations, including exact artifact baselines
and recorded producer observations for selected generated data. It does not
reinterpret an unchanged-content locator move as proof of the locator used by a
historical run.

Approve renames, splits, and merges before they change document boundaries.
Keep shared entry material in the parent entry folder and update affected
summary links, citations, commands, evidence records, and presentation markers
together. Cross-entry moves name every affected record explicitly and report
any destination producer reruns that must finish before the reorganization is
complete. The tools do not edit Markdown or infer what should move.

If removal would replace experimental content, request Replace explicitly
instead.

### Replace

Use Replace only when you explicitly want a named experimental section and its
owned material to supersede work you no longer intend to retain in the active
log. The section you name may change in its `Background:`, `Steps:`,
`Results:`, and `Observations:`, together with the corresponding evidence
records and presentation markers and the exclusively owned scripts or artifacts
needed for the replacement. Leave
other labels unchanged unless you include them in the request.

Preserve every decision exactly. If the replacement removes or contradicts the
stated basis for a decision in the section you named, prefix that decision
with **Needs update:** without rewriting or deleting it. If the effect is
uncertain, leave the decision unchanged until you decide how to proceed.

Before changing the active log, inspect later sections in the same entry and
search for direct links, commands, data inputs, evidence associations, or
artifacts that depend on the material being replaced. Everything outside the
section you named and its exclusively owned support material is read-only.
Finding a dependency does not authorize changing or deleting it. If the
replacement would require any out-of-scope change, expand the request before
continuing. Summary changes require a separately requested Update Summary after
Replace.

Choose a durable backup location outside the active research log. If the
project has no established location and you did not name one, choose a location
before continuing. Before the first overwrite or deletion, copy every affected
document in full and every support file that could change or disappear, then
check that the backup is complete and readable. The backup remains unless you
separately request its removal.

After the backup is secure, produce and check the replacement before removing
the superseded material. Delete only material explicitly included in the
approved boundary, and delete it last.

### Update the summary

Run Update Summary only when you want current understanding or `## Follow-ups`
to change. Read the relevant entries, then revise `<log>.md` by topic rather
than by date. Collect follow-ups only from explicit entry `Follow-up:` items;
do not infer them from general discussion. Keep detailed methods, commands, and
caveats in entries. Preserve established framing, the AI-use disclosure, the
fixed validation and reproduction report links, and every generated report
file exactly.

### Review

Semantic review is an optional, researcher-directed operation. Because it can
require substantial reading and judgment, agents do not add it to another
operation or use it as an automatic completion check. You may request an
Analysis Review, Evidence Review, Record Review, complete semantic review, or a
more focused review lens. An unqualified request to review a log produces a
short choice of review groups rather than silently starting a broad audit.

Review returns findings before making changes. It may judge whether the
requested method, evidence, claim, presentation, or record is sound, but it does
not decide what the maintained account should accept. Request work on the
findings explicitly after reading them; the agent can then switch to the
appropriate research operation.

### Validate

Mechanical validation uses code to check presented computational evidence,
its declared sources, its visible command relationships, and unused retained
material. It does not run research commands, judge scientific meaning, or
perform reproduction. Semantic review and reproduction are separate workflows.
Validation reports precise problems but does not repair research content.
A later correction of a named research-owned finding is a separately
authorized Repair operation.

### Repair

Use Repair only when you explicitly ask to correct a research-owned finding,
batch, finding type, malformed or legacy state, or recognized
interrupted-transaction residue. Direct correction language such as “fix” or
“resolve” is enough when the target and corrected state are clear; diagnosis
alone remains read-only. A campaign works through repair batches formed first
by mechanically proven shared repair relationships. Related findings may cross
finding types or entries. A narrow fallback consolidates otherwise-singleton
Orphans findings with the same exact entry and diagnostic code for repair
orientation; other findings without a proven shared relationship remain
singleton batches. The fallback does not establish a shared cause or imply
that one bulk correction is safe for every member. Each correction preserves
presented evidence and tolerances; cases requiring new scientific choices
remain for your direction.
Verification reports remaining findings or missing coverage without publishing
a new full-log validation. Full validation remains a separately requested
operation.

## Entries and section types

### Entry names and ownership

Entry folders use:

```text
<start-date>-<entry-id>-<descriptive-topic-slug>
```

Use local dates, stable IDs such as `e001`, and short topic descriptions in
ordinary words. IDs increase within one log and are never reused, even after an
entry is removed. The folder name may change when the topic description becomes
misleading; the ID does not.

The entry that creates a saved result owns it. Later entries link to that
result or declare it as a named `data.json` input when a recorded command
or evidence record consumes it. A transformed result belongs to the later
entry that created the transformation.

### Entry documents

The normal entry document is `<entry-id>.md` and begins:

```md
# <Start Date>: <Topic>
```

Use descriptive `##` headings. Each section should answer one research question
or a closely related set of questions. Continue a section when work extends the
same comparison. Start a new section when the question, comparison basis, or
decision context changes but the work still belongs to the same entry.

### Section types

Each entry section has exactly one of three types. An experimental section
contains both `Steps:` and `Results:` and generates new evidence. A synthesis
section contains `Findings:` without experimental labels and records
inspection, comparison, audit, or synthesis of existing material whose
scientific content you validate. A prose section contains no block labels and
provides contextual or connective information. Only experimental sections can
contain entry evidence targets.

Synthesis and prose sections add no mechanically presented evidence or evidence
records, and changes confined to them do not make validation out of date. A
synthesis
may preserve external evidence or selected findings from discarded internal
investigations whose experimental records and supporting files are not
preserved. You remain responsible for validating this non-primary material. If
an internal experiment or its supporting files are preserved, document that
experiment and its evidence in an experimental section; a separate synthesis
may refer to it.

Any other label combination is structurally invalid. Validation skips the
entire invalid section, identifies the entry and heading, and records one
structural failure so the skipped content cannot coexist with an all-clear
validation outcome. It does not infer the intended section type or partially
validate the section. A separately requested Research-Log Conformance review
may help explain the misuse; correction remains a separately requested
operation.

### Labels

Write each label on its own line in inline code and use the table order below.
Do not add an empty label or invent a synonym.

| Label | Use |
| --- | --- |
| `Background:` | State the question, motivation, prior state, hypothesis, and conditions needed to interpret the section. |
| `Steps:` | In an experimental section, record the commands, scripts, inputs, settings, and analytical actions needed to understand or reproduce the result. |
| `Results:` | In an experimental section, present measurements, tables, figures, files, and other outputs produced by the recorded steps. |
| `Findings:` | In a synthesis section, record understanding from inspecting or combining existing material without introducing a new calculation. |
| `Observations:` | In an experimental section, record patterns or interpretations grounded in that section's results. Treat agent-drafted observations as drafts until you review them. |
| `Uncertainty:` | Record only uncertainty you intentionally retain with a result or decision; do not use it for routine caveats or unfinished work. |
| `Decisions:` | Record your decisions and their supporting evidence or constraint. Mark proposals and provisional choices explicitly. |
| `Follow-up:` | Record deferred work you want carried into the log-level follow-up list, not current planned work or speculative ideas. |

Experimental sections require `Steps:` and `Results:` and may also use
`Background:`, `Observations:`, `Decisions:`, `Uncertainty:`, and
`Follow-up:`; they never use `Findings:`. Synthesis sections require
`Findings:` and may use `Background:`, `Decisions:`, `Uncertainty:`, and
`Follow-up:`. Prose sections use no labels.

Do not add a `Validation:` label to an entry. Validation status belongs only in
the generated validation report.

### Compact examples

Experimental section:

````md
## Candidate comparison

`Background:`
Compare the retained baseline with the proposed correction.

`Steps:`
```bash
./pyrun scripts/compare_candidates.py \
  --input "<test_set>" \
  --output-csv data/comparison.csv
```

`Results:`
The correction reduced median error from
`0.292%`<!-- eid:baseline-median-error --> to
`0.286%`<!-- eid:trial-median-error -->.

`Observations:`
The effect is small but consistent across the retained cases.

`Decisions:`
- Retained the correction for the next evaluation stage.
````

Synthesis section:

```md
## Evidence synthesis

`Background:`
Compare the retained experiments and the cited calibration study.

`Findings:`
- Both sources identify temperature drift as the dominant limitation.

`Uncertainty:`
- It has not been established whether the same relationship holds outside the
  tested range.
```

Prose section:

```md
## Scope

This entry covers the detector calibration used for the May observing run.
```

### Writing and preservation

Lead with the question, comparison, or decision rather than run chronology.
For repeated experiments, keep the baseline, candidate, measured benefit,
relevant cost, and tested boundary together. State quantities, units, scope,
and limits instead of relying on words such as “better,” “stable,” or “did not
work.”

Do not use entries to narrate which files or tools an agent opened, checked, or
left unchanged. The fact that routine work occurred is not research evidence.
Keep only the procedure needed to understand or reproduce the result and any
quality finding that materially affects it.

Keep evidence, observation, interpretation, decision, uncertainty, and
validation status distinct. Use “retained,” “accepted,” or “validated” only
when that status is established; otherwise use “proposed,” “provisional,”
“planned,” or “awaiting validation.”

Preserve exact values, units, variable names, commands, paths, citation keys,
and stated uncertainty. Do not rewrite dated evidence as though later results
were already known. Retain negative evidence only when it still explains a
result, decision, or useful lesson.

## Scripts, data, and reproducing results

### Where supporting material belongs

Keep material with its narrowest real owner:

| Used by | Location |
| --- | --- |
| One entry, including its split documents | The entry folder |
| Several entries in one log | `<log>/scripts/` or another log-level location |
| Several logs or production work | Project code |

During an active investigation, before implementing a script, check whether the
project already provides the needed data access or behavior. When the choice
changes what the evidence establishes, decide whether to use that project
interface, test it directly, or bypass it for independent evidence. Record the
choice in the entry.

Do not copy shared code into entries. If later changes to shared code would
change a recorded result, preserve the old interface or add a versioned one.
Snapshot only a small entry adapter or settings file when a fixed record is
necessary.

### Recorded commands

During an active investigation, record the commands used to produce or analyze
a saved result, figure, table, or check. Put them in a `bash` code block under
the experimental section's `Steps:` label and write them as though the entry
folder is the current working directory. Writing and running these commands is
part of doing the original research; it is not an independent validation or
reproduction check.

The command conventions below apply to commands created or revised during an
active investigation. When incorporating completed work, record the actual
command, environment, and settings as far as they are known, even if they do
not follow these conventions. Do not replace them with an invented command or
rerun them merely to make the record conform. State any missing information or
material as a limit on later reconstruction.

Use an explicit command for a one-off invocation. Finite repeated commands may
use simple literal shell structure, but keep the values and material paths
needed to understand each invocation mechanically visible. Validation does not
execute shell or guess through dynamic shell behavior; it reports unsupported
command structure rather than inferring relationships from it.

For Python, use the entry-root `./pyrun` launcher. Before loading its Python
implementation, it uses `<project>/.conda/bin/python` when that environment
exists; otherwise it uses a supported `python3` available to the caller. It
also expands these path tokens:

- `<project>`: project root;
- `<log>`: research-log directory;
- `<name>`: one exact file or directory input, or the local locator for a
  pinned Git repository, in the owning entry-root `data.json`;
- `<name:commit>`: the exact pinned commit paired with a registered Git
  repository locator; and
- `<directory-name>/member`: one exact member of a declared directory input.

Data tokens occupy the complete input argument. Quote arguments containing
angle tokens.

With no runner options, pass a Python script directly and omit `--`. Its
filename stem becomes the effective command ID (CID). Any runner options must
precede `--`, with the script on the following line. For another independent
use of the same Python program, `--cid 2` resolves to the full effective CID
`PROGRAM_STEM-2`; only that full ID is stored. Use a full `--cid CID` override
for multi-program owners, non-Python or invalid program names, or identity
preservation across a program rename. The effective CID owns exactly one command or bounded loop,
every loop expansion reuses it, and each fence contains one such owner. See the
[detailed command-writing guidance](../skills/research-logging/references/file-entry-commands.md#write-a-recorded-command).

For an active Python workflow, the entry uses a symbolic link named `pyrun`
that points to the installed launcher; do not copy the launcher into the log.
If the declared project environment or symbolic links are unavailable,
explicitly approve and record an exception rather than silently using another
interpreter.

Expose settings that affect the result as named command options: data split,
cases, seeds, sample count, physical or numerical controls, and all saved
outputs. Keep entry-local paths relative. Use `<log>` only for shared log
material and `<name>` for indexed inputs. Put one option per line for a
nontrivial command:

```bash
./pyrun scripts/run_study.py \
  --input-dataset "<development_set>" \
  --candidate baseline \
  --candidate trial \
  --seed 123 \
  --samples 500 \
  --output-summary-csv data/study-summary.csv
```

Make evidence-relevant input and output relationships mechanically visible.
Prefer natural option names that make input and output paths obvious. When a
real `pyrun` interface cannot do that naturally, use `--other-inputs` or
`--other-outputs` before `--` to list its comma-separated option names or
one-based positional selectors such as `@2`. The runner infers file or
directory kind from the registered input or completed output. Researchers
should not have to reshape a natural command merely to satisfy validation.

Run a new or changed script through the recorded command from the entry folder
to produce or check its saved outputs before presenting them as results.
Within the CID, `pyrun` derives one stable execution ID from the expanded
child-parameter vector and records the complete recipe and its output set,
current script, parameters, inputs, and bytes separately. It also records one
effective-code fingerprint for statically reachable Python behavior across the
current Git project. The analyzer parses normalized syntax without importing or
executing project code. Comments, formatting, unreachable definitions, and
external package implementation do not affect the fingerprint. The raw script
fingerprint remains useful provenance but does not make reproduction stale by
itself.

Research commands use ordinary Python imports. `pyrun`, command verification,
reproduction, and effective-code analysis share one import order: the executing
script directory, entry `scripts/`, log `scripts/`, then the project root. Put
shared code at the narrowest owner described above; do not add `sys.path`
bootstrapping or an authored `PYTHONPATH` to recreate these roots.

Some dynamic behavior cannot be fingerprinted safely. In that case `pyrun`
still executes and quietly records no effective-code fingerprint. Command sync
also succeeds when fingerprinting is unsupported or analysis fails, reporting
a bounded warning with the location and explaining that code currentness
remains unavailable until the source is made analyzable and the command is run
again. Reproduction selects a missing,
mismatching, or unavailable current effective-code fingerprint. When current
code cannot be fingerprinted, every incremental plan selects it again because
unchanged currentness cannot be established; this is not a block.
Use the `--auto-reproduce=false` runner option for simulation, model training,
and comparable commands that should not run during automatic reproduction.
Use the `--exclusive` runner option when managed reproduction must run the
command alone across all active reproduction runs in the project. In both
cases, put the runner option and `--` on the `./pyrun` line and the script on
the following line. Exclusivity is a scheduling policy, not part of the recipe
identity, and it does not change ordinary direct execution or reserve unrelated
host processes. For a later policy-only change, edit Markdown first and run
`log command sync` with `--path`, `--entry`, and the effective `--cid`. Current
execution state must use `research-log-pyrun/v7`; earlier schemas are
unsupported.
When stdout or stderr is retained as evidence, use
`--capture-stdout`, `--capture-stderr`, or `--capture-stdout-stderr` as a runner
option before `--`; raw `tee` or redirection cannot create that current output
record. Output available only in an agent's temporary context is not evidence.
This is original research execution, not validation or reproduction; do not
rerun an unchanged command solely to test reproducibility or Provenance.

For corrections to a recorded command, edit its Markdown first, then use the
[command synchronization workflow](../skills/research-logging/references/file-entry-commands.md#synchronize-a-recorded-command)
to update its declarations and execution records. The CLI verifies the complete
CID without changing Markdown or retained files.

### Input registry

Use entry-root `data.json` for every file, directory, or pinned Git repository
consumed as a material input by a recorded command or evidence record. Each has
one stable name, local location, identity rule, and Boolean `origin`. An
origin stops the Provenance chain at that artifact or tracked commit snapshot;
generated material continues to its unique earlier producer regardless of
where the file is stored.

Register an accessible producerless input through the active skill's management
CLI:

Author the Markdown command first, then synchronize it together with any
missing declarations:

```text
<skill>/scripts/log command sync --path LOG --entry ENTRY --cid CID
  --add-origin development_set=/data/project/development.csv
  --add-generated-directory results=data/results
```

File and directory variants make the intended kind explicit. Generated outputs
can be declared before they exist, provided the command uniquely owns them.
Repeated consistent assertions are accepted; known declarations may be omitted.
A Git origin uses --add-origin-git NAME=COMMIT:PATH, and commands pass both
the repository token and its paired commit token. A later same-log entry uses
its own sync's --add-from-entry NAME=ENTRY instead of copying data or inventing
an origin. Shared target changes, directory identity, kind, boundary, and
reproduction policy belong to `log data update`. Renames and deletion use
`log data rename` and `log data delete` after Markdown uses are updated or
removed and their owners synchronized. These actions never delete retained files.
Normal recording and repair use the owning CLI, not direct registry edits.

One `pyrun` output-directory declaration represents one atomic generated
artifact when that invocation owns the complete directory. Register the
directory once. A whole-directory consumer uses `<name>`; an exact member
consumer or evidence source uses `<name>/member`. The member association stays
exact while execution observation and Provenance use the directory's declared
identity rule and the corresponding recorded execution fingerprint. A linked
image or download has its own exact-file acceptance baseline in `evidence.json`,
even when its source is a directory member. Large origin or generated directories may select bounded identity
files or final-component patterns when the selected files explicitly define
their relevant identity; excluded descendants are not covered.

Input targets passed to `log data` are absolute or relative to the selected
entry root, regardless of the caller's working directory. Prefer short
entry-relative targets such as `data/development.csv` for entry-owned material.

Use `retention.json` only for intentionally retained material outside the
evidence-rooted graph. Retention affects orphan classification and cannot
create evidence or repair provenance.

### Scripts and saved outputs

Scripts receive input and output paths from command-line options rather than
hard-coded project or log paths. For expensive, stochastic, or multi-use work,
separate generation, analysis, and plotting so later stages read saved
intermediate files instead of repeating the expensive step.

During an active investigation, before saving a figure, fail on missing cases,
non-finite values, or incompatible units. Inspect every saved figure for
missing series, clipping, overlapping labels, unreadable legends, and wrong
units. Record defects, corrections, or limitations that affect the evidence;
do not narrate a routine successful inspection.

During an active investigation, reload any serialized file that a later command
consumes and check its expected structure. Include its shape, row count, or
schema version only when that information helps a researcher understand, reuse,
or assess the evidence. Record a checksum when a binary or externally mutable
file is the fixed basis of a retained result.
Apply these checks only to material created, changed, or consumed by the current
investigation; do not turn them into an entry-wide or log-wide audit. For
completed work, preserve checks that were actually performed and identify what
remains unknown rather than repeating them for documentation.

### Origin inputs and references

For an origin workflow input, record what it is and how it was used. Keep or
materialize locally accessible files and directories so validation can confirm
their current bytes. For a pinned Git repository, keep an accessible repository
containing the declared commit; its path is only a locator, and the commit
identifies the tracked source snapshot. Provenance stops at that declared
artifact or commit; a researcher-directed Source Authority review, not
validation, determines whether the source is trustworthy.

Use optional `<log>/refs.bib` for papers, documentation, and other cited
sources. Verify new bibliographic details against an authoritative source and
keep citation keys stable. Cite where the reference is used:

```md
[`smith2024`]
[`smith2024`,`lee2025`]
```

Viewing or considering a reference does not add it to the log. Add BibTeX and
a citation only after you accept or request the reference. Keep notes about why
it matters in the entry, not in `refs.bib`.

## Results and supporting evidence

Computational results belong in experimental sections and must be supported by
retained material from the recorded research workflow. Common presentations
include numerical results in prose, Markdown tables, excerpts of saved command
output, figures, and links to retained artifacts.

Present the result naturally. Keep the quantity, units, uncertainty, and
comparison clear enough that another researcher can understand what was
measured or derived. A derived value must already exist in retained output; do
not calculate a new result only while drafting the entry.

Retain the source material needed to check the presentation. One source may
support several results, and one result may draw on several sources. Structured
results should retain structured data when practical rather than only formatted
text. Figures and linked artifacts must connect to the recorded command that
produced or used them.

The research-logging workflow maintains supporting evidence metadata alongside
the entry. That metadata connects each presented result to its retained sources
and records permitted presentation steps such as selection, rounding, units,
uncertainty, or table assembly. Researchers do not need to author or inspect its
technical syntax during normal work. Review and validation report when the
connection is missing, ambiguous, unsupported, or inconsistent.

The agent writes an EID comment describing sources, selection, and formatting
beside the presentation, then calls `log evidence sync --id ID`. New values
use empty code spans; direct tables use an authored header and alignment row;
retained-output excerpts use an empty text fence and explicit source bounds.
The same format is used for subsequent edits. Sync derives the evidence record,
fills the presentation, and accepts current linked-artifact fingerprints.

After an artifact is updated by `pyrun`, the agent runs
`log evidence compare --source NAME` once to inspect exact before/after
presentations, judges the changes, then runs `log evidence sync --source NAME`
once to update related entry evidence and forwarded summary values.
It still syncs unchanged presentations to refresh fingerprints without rewriting
Markdown. Either call also supports one evidence ID instead of an artifact.

There are no temporary definition files or generic advanced-table language.
A direct table can select/reorder source columns and format their values.
A summary table is ordinary Markdown with independently evidenced cells.
Each numeric or closed-Boolean data cell needs its own scalar or compound EID;
partial marking does not replace a whole direct-table declaration.
Joined or derived tables require a recorded script to emit a retained
presentation-ready artifact first. Scientific computation does not live in
the evidence comment.

Every presented generated result must trace through the recorded workflow until
it reaches an explicit origin or an inputless producer that does not require
reproduction. Every reached
generated output must match `pyrun`'s current output and script fingerprints,
exact ordered parameters, and direct-input fingerprints. This bounded
Provenance result does not claim causation, complete dependency capture,
scientific validity, or reproduction.

Material may also be retained intentionally for later investigation even when
it is not used by a current result. Tell the research-logging agent why it is
being kept so that it can be distinguished from an accidental orphan. This
does not turn the material into evidence.

The agent records that intent through:

```text
<skill>/scripts/log retention add --path <log> --entry <entry-id> \
  --id <id> --target <target> [--target <target>]... [--reason <reason>]
```

The action accepts either one nonempty directory or one or more regular files
and owns the retention registry. Normal Record does not edit or inspect that
registry directly.

### Summary evidence

The maintained summary reports current understanding; it does not originate new
computational evidence. A numerical result in the summary must already be
supported in an experimental entry. If the summary needs a differently rounded
value, different units, or another derived comparison, establish that
presentation in an entry first.

The research-logging workflow maintains the association between a summary
result and its supporting entry evidence. Researchers should review
whether the summary wording preserves the meaning and limitations of the entry;
mechanical validation checks only that the recorded values and associations
remain consistent.

Record and Replace maintain entry evidence within their authorized scope.
Update Summary maintains summary associations. Review and Validate report
problems but do not change the research record.

## The current summary

`<log>.md` is the current view of the research. Immediately below its title it
links separately to the generated validation and reproduction reports. It then
starts with `## Contents`, `## Entries`, and `## Summary`. During Update Summary, add
`## Follow-ups` only when entries contain intentional `Follow-up:` items, and
add its Contents link at the same time. Every summary ends with `## AI Use`.

```md
# <Log title>

Validation: [latest completed report](<log>/validation.md)

Reproduction: [latest report](<log>/reproduction.md)

## Contents

- [Entries](#entries)
- [Summary](#summary)
- [AI Use](#ai-use)

## Entries

- `2026-05-01` [Calibration drift check](<log>/entries/2026-05-01-e001-calibration-drift-check/e001.md)

## Summary

- <Current result, decision, limitation, or unresolved point with an entry link>

## AI Use

<Disclosure>
```

`Entries` lists every entry. For a split entry, use one dated parent bullet and
indented links to `e001a.md`, `e001b.md`, and any other documents. Keep the list
complete when entries are added, renamed, split, merged, or retitled. Link text
matches the entry title without its date prefix; a split entry's parent text
matches its folder topic.

Write `Summary` as short, topic-grouped bullets. Lead with current understanding
rather than the history of how it developed. Keep one result, decision,
limitation, validation boundary, or unresolved point per bullet and link
important claims to their supporting entries. Keep methods, commands, detailed
evidence, and long caveats in the entries.

Carry forward only understanding and limitations that remain current. Do not
promote a proposed or agent-drafted conclusion unless you have accepted it.
Update `Follow-ups` during Update Summary, not while recording an entry.
Include only explicit entry `Follow-up:` items unless you add a log-level item
explicitly, and do not invent follow-ups from general discussion.

A current summary may present marked statistics but does not originate new
statistics. Each presented summary statistic must match the meaning of evidence
in one experimental entry section. The research-logging workflow maintains that
association. Research-log review determines whether qualitative summary points
are supported by entries. Summaries do not contain tables, images, blocks
showing saved command output, or links to saved files.

### AI-use disclosure

The final `## AI Use` section describes how researchers and agents worked on
the log. Preserve customized wording unless you choose to change it. A new log
starts with:

```md
## AI Use

The researcher has led and reviewed the scientific work throughout, chosen the
methods and next steps, and made or approved the observations and decisions
recorded in this log. Under the researcher's direction, agents have mainly
helped implement and run code, document the work, check calculations and
outputs, and draft observations for review. They have also helped find and
summarize relevant research, explore solutions, and challenge the researcher's
reasoning. The researcher has checked all claims and conclusions against
original sources, simulations, or saved data. Generative AI has not been used as
scientific evidence. Reported computational results have come from code run on
saved source data with documented settings. The source data, settings, and
outputs have been kept so the results can be checked and reproduced.
```

Do not add entry-level `AI Use:` labels. This internal disclosure does not
replace disclosure rules from a journal, institution, funder, or venue.

### Generated report links in the summary

Place these exact navigation lines immediately below the level-one title, with
one blank line after each:

```md
Validation: [latest completed report](<log>/validation.md)

Reproduction: [latest report](<log>/reproduction.md)
```

The links are stable research-document scaffolding. They contain no date,
status, failure count, artifact-currentness claim, or contract version. Do not
add Validation or Reproduction items to `## Contents`. A new log initializes
its reproduction report as not yet reproduced; before the first validation,
the validation link may point to a report that does not yet exist.

Record, Replace, Reorganize, Repair, and Update Summary preserve both links
exactly and never edit generated reports. Validate reads the maintained summary
but never changes it. The generated `<log>/validation.md` contains only
mechanical-validation state. The generated `<log>/reproduction.md` contains
only reproduction artifact and run state. Neither report supplies a combined
conclusion.

## Reviewing a research log

Semantic review examines a research log without executing its workflows or
changing the maintained account. It runs only when you request it; agents do
not add it as a routine check of their own work.

Three broad review groups are available:

1. **Analysis Review** asks whether the methods, implementation, result
   derivations, statistical reasoning, and numerical work are sound.
2. **Evidence Review** asks whether sources, evidence, claims, decisions, and
   evidence presentations are trustworthy and adequately supported.
3. **Record Review** asks whether the research record is current, consistent,
   reconstructible, conforming, and clearly written.

You may request one or more groups, a complete semantic review, or a focused
lens such as Citation Support, Evidence Selection, Summary Fidelity, or
Presentation Consistency. If you ask only to review the log, the agent presents
the three groups and an option to choose from the complete lens catalog.

A review normally returns numbered Issues, Improvements, and Unverified points
ordered by consequence. Each finding identifies its lens and durable location,
explains the problem in readable prose, and recommends a bounded next action.
The report does not assign a semantic pass or approval status.

Research-Log Writing review applies the scientific prose discipline of the
science-writing skill together with the log's own writing conventions. Review
of scientific prose outside a research log belongs directly to science writing;
general software-quality review remains separate from review of whether code
faithfully implements a recorded research method.

Review, validation, and reproduction remain separate. Validation uses code to
check declared relationships and exact presentations; reproduction reruns
recorded workflows; semantic review reads and judges the requested meaning or
soundness. When the intended operation is unclear, the agent asks which you
want. After a Review report, you retain authority over methods, interpretations,
conclusions, decisions, accepted synthesis, and research direction. Ask
explicitly when you want findings addressed.

## Reproducing a research log

Reproduction is a mechanical background workflow for one maintained log or
entry. It reads recorded JSON recipes and the research graph, executes retained
scripts and inputs in place under confinement, and writes regenerated outputs
and diagnostics into a project-local run folder. It does not use Markdown as
execution authority, judge scientific meaning, copy the project or change
research prose, commands, evidence declarations or retained artifacts.

Whole-artifact type-aware exact comparison is the default. An explicitly
authored evidence-scoped comparison may permit nondeterministic file content;
numeric tolerance belongs to the individual evidence record. A scoped match
does not claim whole-file equality. The CLI never guesses comparison exceptions
from changed outputs.

Preview current work or launch it explicitly:

```bash
<skill>/scripts/log reproduce plan --path <log> [--entry <entry-id>] \
  [--recheck] [--jobs <positive-integer>] [--execution-timeout-seconds <seconds>]
<skill>/scripts/log reproduce run --path <log> [--entry <entry-id>] \
  [--recheck] [--jobs <positive-integer>] [--execution-timeout-seconds <seconds>]
```

Plan is a bounded, read-only current-work view; follow its cursors for more
rows. Ordinary lock infrastructure is its only filesystem side effect.
Launch prepares afresh and publishes fresh completed validation before
acceptance. With runnable work it returns a durable run ID while the detached
CLI-owned job continues. With no runnable work it returns current
reconciliation and normally creates no job, saved result or report.

Selection defaults to incremental. Commands not requiring reproduction need
no saved result; unchanged previous failure/block or completed unequal/uncomputed
comparison is not retried. Add
`--recheck` to retry eligible work, without bypassing blockers or automatic
policy. Artifact matching does not decide whether execution is needed.
`auto_reproduce: false` commands are excluded by default; add
`--include-all` only when you explicitly authorize their additional cost.
Include-all and recheck are independent.

An entry target never executes an outside-entry producer; a log target never
imports commands from another log. Attributable source, input, boundary,
baseline or validation-admission problems block related work while independent
commands remain eligible. Unsafe or unlocalizable authority can refuse the
whole operation. Currentness remains Reproduce-owned, not a new validation
finding.

A successful producer's outputs are compared before consumers run. A matched
artifact permits only consumers that use that artifact. A differing artifact or
one that cannot be compared blocks those consumers and their dependants, while
other outputs and independent commands continue. The run completes and saves
these artifact and command outcomes normally; it does not convert a local
comparison result into an operational failure.

The jobs cap defaults to 1; dependencies, overlapping path claims and
project-wide exclusivity may reduce actual concurrency. Each command defaults
to a 300-second wall-clock limit; timeout terminates its supervised tree and
leaves independent work eligible. Accepted settings cannot change on resume.
Current execution authority is `research-log-pyrun/v7`; older schemas are
unsupported.

Observe or control the accepted job by run ID:

```bash
<skill>/scripts/log reproduce status --path <log> --run-id <run-id>
<skill>/scripts/log reproduce stop --path <log> --run-id <run-id>
<skill>/scripts/log reproduce resume --path <log> --run-id <run-id>
```

Status is an observational lifecycle snapshot, separate from command/artifact
outcomes; it does not inspect processes or initiate recovery. Agents and
confirmed optional monitors use `status --json`. Monitoring never controls the
job. Stop records durable intent and waits for the stopped result, another
terminal result, or an exact recovery/control failure. It preserves completed
results and diagnostics. Resume uses the same accepted plan and settings,
never replans, and launches only never-started or stopped nonterminal work
after cleanup. Durable success/failure is not attempted again. Ordinary
operationally failed runs cannot resume; failed frozen publication can retry
from its journal without execution. Source changes require a new run. An
unrelated log's run does not block planning merely because it needs recovery.

Runs live at
`<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>[-<entry>]-<run-id>/`.
The immutable UTC acceptance date organizes paths; every lookup uses run ID
alone. Obsolete jobs are unsupported and remain unchanged, not migrated.

Complete production and comparison clear the existing reproduction requirement,
even for unequal artifacts. If every artifact is canonically matched, the same
atomic update adopts the accepted raw-script and effective-code observations.
The effective-code observation may be null; that adoption does not make
unfingerprintable code current, so later incremental plans still select it.
Unequal or uncomputed comparisons preserve the prior source and retained output
observations. Failed, partial, blocked or stopped work cannot reconcile early.
A later operational/publication failure does not undo an already-completed
eligible command's reconciliation.

Inspect immutable saved results:

```bash
<skill>/scripts/log reproduce show --path <log> [--run-id <run-id>]
<skill>/scripts/log reproduce show --root <project>
<skill>/scripts/log reproduce list commands --path <log> [--entry <entry-id>] \
  [--status <status>] [--reason <reason>] [--run-id <run-id>]
<skill>/scripts/log reproduce list artifacts --path <log> [--entry <entry-id>] \
  [--cid <cid>] [--status <status>] [--reason <reason>] [--run-id <run-id>]
```

Show combines command selection/execution and artifact comparison into separate
hierarchies; root show uses two compact tables. They have different units:
one command may produce many artifacts. A dash means unavailable, not zero.
Omitted run ID selects the latest saved target, never current registries.
The no-work launch's reconciliation is already its own result; do not replace it
with an older saved summary.

Use each list row's exact `log reproduce detail command|artifact` invocation
for retained invocation, contributing causes, expected/regenerated values,
retained/accepted source observations, output locations and available diagnostic tails. Lists and detail are bounded,
with section/cursor continuations; text and JSON share the saved facts.
Missing diagnostic files qualify availability, not saved classification.

The generated `<log>/reproduction.md` contains only single-log show's compact
summary. `log reproduce render --path LOG` recovers that report without
replanning or execution. The disposable result domain is replaced only by an
explicit rerun, not migrated; use `log reproduce run --path LOG --recheck`
when old history is unsupported. A genuinely empty whole-log recheck may
replace obsolete history with an empty-confirmation receipt and report, without
a fabricated run. Policy-skipped or blocked work is not empty.

For one current repaired invocation, use
`log command verify --path LOG --entry ENTRY --cid CID --execution-id ID`.
It is isolated and synchronous, retains private outputs/diagnostics once its
workspace exists, and publishes nothing. It reads current prerequisites,
baselines and authored comparison rules, reports direct-input observation
differences without adopting them, and preserves metadata/results completely.
It cannot clear requirements, resume, promote or adopt changed recipe
parameters/declarations or newly observed effective code.

No validation runs automatically after reproduction publication. Run Validate
explicitly when a current validation outcome is required; it does not rewrite
historical reproduction outcomes or restore cleared requirements.

Regenerated outputs never replace research baselines automatically. Under your
explicit direction, `log reproduce promote --path LOG --run-id RUN --cid CID --execution-id ID`
copies one complete related staged output set with baseline, confinement,
reservation and rollback guards, and installs that run's accepted source
observations with the new output fingerprints. It leaves staged files and saved
outcomes intact.

The [reproduction specification](research-log-reproduction-spec.md) owns the
exact CLI, records, resource bounds and lifecycle contract.

## Validating a research log

Mechanical validation is a code-only check of the recorded research log. It
does not rerun the research, judge scientific meaning, or decide whether a
result is persuasive. Those questions belong to reproduction and scientific
review.

At a high level, validation checks four things:

- the research log and its supporting metadata are structurally consistent,
  including input declarations, origin boundaries, and intentional retention;
- presented computational results match their declared retained sources;
- generated evidence can be traced through recorded command, output, script,
  and input support to explicit origins; and
- retained files and output records are connected to the recorded work,
  intentionally kept, or reported as Orphans findings.

Whether a recorded execution remains current belongs to Reproduction and does
not create validation findings or blocked checks.

### Running validation

Resolve `scripts/log` from the active research-logging skill package and name
the logical log path:

```bash
<skill>/scripts/log validate run --path <log>
```

Use `<skill>/scripts/log validate run --root <project-root>` for every maintained
log returned by canonical bounded discovery beneath one project. The cross-log
run includes one row per discovered log and isolates operational failures.

Use `log validate show --path LOG` for one saved full-log summary or `log
validate show --root PROJECT` for the project view. Use `log validate list
findings --path LOG`, `log validate list batches --path LOG`, `log validate
list blocked --path LOG`, or `log validate list failed --path LOG`. Use
`log validate detail finding --path LOG --id FINDING_ID` or `log validate
detail batch --path LOG --id BATCH_ID` for saved diagnosis. These commands do
not reevaluate research files. Finding detail states the saved issue
explanation and labels the complete diagnostic, including the actual and
expected states for a comparison defect, so the problem can be identified
without inspecting validator source. `log validate render --path LOG` rebuilds
`validation.md` from the saved snapshot.

Each applicable check is exactly Pass, Finding, Blocked, or Failed. Blocked
means a validation finding or failed check prevented the rule from running;
the saved row names those root blockers. Failed means the validator could not
complete that localized check reliably. Inapplicable rules create no check.

Root-scoped text summaries identify each log by its final directory name. Use
`Mon D` UTC dates to keep text tables compact. Use JSON when a consumer needs the
canonical full path or exact timestamp; single-log text views also retain the
full path.

A completed snapshot is Clear, Findings, or Failed. Localized failed checks are
saved and independent checks continue. Whole-operation failures such as
capacity exhaustion or a source change across the operation boundary publish
nothing and preserve the prior snapshot.

One completed evaluation reports every independently checkable failure in the
bounded evidence-rooted graph. A missing or ambiguous relationship stops only
the affected edge; validation continues through the graph's other inputs,
evidence artifacts, and entries. This avoids requiring successive validation
runs merely to reveal deeper unchanged problems.

A shared provenance defect is one finding at its natural material or
relationship subject, even when several evidence records consume it. The
research graph retains those consumers for impact and repair context; they do
not multiply the finding inventory. Different unsatisfied rules or different
subjects remain distinct findings.

Every finding belongs to one deterministic repair batch. Batches may cross
finding types when findings share a concrete repair relationship. Residual
singleton Orphans findings may be grouped only when they share the exact entry
and orphan code.

A completed published mechanical evaluation writes the human-facing
`<log>/validation.md` report. It shows when mechanical validation last
completed, records the saved outcome, counts atomic findings under
Conformance, Evidence, Provenance, and Orphans, and counts repair batches,
blocked checks, and failed checks. Beneath its heading it contains only the
same compact field/value table as single-log `log validate show`, with explicit
zeroes and a compact UTC saved date. Inventories and diagnostics remain on
validation `list` and `detail`, not in the report. Reproduction publishes its separate
`<log>/reproduction.md` report; neither report hides the other's failures.

The single-log summary includes blocked and failed counts. The cross-log table
keeps only finding-type and batch counts, then reports aggregate Blocked and
Failed totals below it. If any discovered log lacks a readable snapshot, those
totals are marked partial and name how many logs contributed.

The Markdown reports are derived human surfaces. The tools maintain their
detailed, rebuildable query state in `<log>/.cache/results.sqlite`; a fresh
checkout or cleared generated query state requires validation or reproduction to rebuild
it. Clearing it does not disturb a durable reproduction run or `pyrun.json`.
Ask the agent
to inspect a finding rather than editing generated files. Validation reads the
research record but changes only its own generated output.

### Resolving findings

Validation identifies problems; it does not repair the research record. A
separately authorized Repair operation corrects the named finding or repair
batch without changing presented evidence or choosing new scientific content.

You can ask the agent to explain a finding using saved finding details,
without rerunning validation. If those details are unavailable, the agent
reports that limitation rather than inferring what an earlier evaluation
established.

After an authorized repair, the agent validates the affected target. Entry
validation preserves the published full-log report and does not replace
full-log validation. A final full-log run is required for whole-log clearance.

Saved finding details may be replaced or cleared. Keep important conclusions
in the research record rather than relying on that temporary generated state.

Recognized obsolete generated-validation files are ordinary Orphans findings.
Validation reports them without interpreting, archiving, removing, or allowing
them to block otherwise independent evaluation.

Research changes do not automatically trigger validation, semantic review,
reproduction, or summary updates. The report represents the latest completed
validation run; the next run determines which prior observations remain
reusable and evaluates everything affected by current research state.

## Command Verification

Use `log command verify --path LOG --entry ENTRY --cid CID --execution-id ID` to test one
current repaired invocation. It is isolated and synchronous: it writes only its
temporary command-verification workspace and lock state, never a reproduction run,
result, report, requirement flag, or promoted artifact. Bare `log reproduce`
plans only log or entry work; former repair reproduction and run-ID
single-execution presentation are removed. Available current direct inputs are
consumed and compared with their recorded observations; differences are
reported without updating those observations. Failures before workspace
creation return a null workspace, while every later terminal outcome retains
the created workspace and diagnostics.
