# Research Logging

This is human-facing researcher documentation for using the
`research-logging` skill. It explains what researchers can ask the skill to do,
what to expect from the workflow and visible research record, and which
research decisions remain theirs.

This document is not a specification for agent behavior, metadata grammar, or
validator implementation. Do not use it as a completeness checklist or proxy
for the `research-logging` skill. The skill is a separate, self-contained and
self-documenting agent surface; it does not depend on this guide. The
mechanical-validation CLI and its supporting tools must instead adhere to
`docs/research-log-mechanical-validator-spec.md`. These three surfaces must be
conceptually compatible, but they have separate authority and do not repeat the
same detail.

## Workflow at a glance

A research log uses five core operations:

1. **Record** research activity in a new or existing log, using numbered, dated
   entries with their supporting material.
2. **Replace** a named experimental section and its owned material when you no
   longer intend to retain the superseded work in the active log.
3. **Update Summary** when you want the current research state and follow-ups
   brought up to date.
4. **Review** structure, presentation, associations, synthesis, and visible
   evidentiary support.
5. **Validate** mechanically that presented computational results match their
   declared sources, have visible provenance, and leave no unexplained retained
   material.

Reference management supports these operations when needed; it is not an
additional stage. Reorganizing the log is part of Record because it revises
existing material without changing entry IDs, research meaning, or links
between results and their sources.

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
generated validation records. The summary describes the current state; entries
and their saved material preserve the detailed research record.

The minimum structure is:

```text
<log>.md
<log>/
  entries/
```

A populated log may contain:

```text
<log>.md
<log>/
  refs.bib
  scripts/
  validation.md
  validation/
  .cache/
  entries/
    2026-05-01-e001-calibration-drift-check/
      e001.md
      data.json
      evidence.json
      pyrun-outputs.json
      retention.json
      pyrun -> <installed launcher>
      data/
      images/
      scripts/
```

Create optional files and folders only when they are needed. Start navigation
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
metadata. It preserves
the fixed report link and every generated validation file. Check outputs as they are produced;
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
follow-ups, and generated validation unchanged.

#### Continue an investigation

Continue an existing entry when the new material extends the same topic or
comparison. If several entries could fit, choose the target entry explicitly.
Continuing does not automatically rename, split, merge, move, or remove
existing material. A new section within the chosen entry is ordinary Record
work, not reorganization.

If the work clearly belongs in a different entry, choose its destination before
recording it. Choosing another existing entry or starting a new one does not
authorize moving earlier material.

#### Reorganize the log

Reorganize a log only when you request it or approve a recommendation from
Review. A recommendation does not change the log by itself. Rename, split,
merge, move, or remove material without changing stable entry IDs or research
meaning. Update all affected links when an entry folder, document title, or
section heading changes. Split an entry only when distinct topics impair
retrieval; length alone is not a reason. Split documents stay in the same entry
folder and use suffixes such as `e002a.md` and `e002b.md`.

Approve renames, splits, and merges before they change document boundaries.
Keep shared entry material in the parent entry folder and update affected
summary links, citations, commands, evidence records, and presentation markers
together.

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
fixed validation-report link, and every generated validation file exactly.

### Review

A review returns findings about the requested text, structure, evidence links,
summary support, or whole log before making changes. Request fixes explicitly,
either with the review or after reading its findings. Review checks whether the
record is complete and internally consistent. It may report an apparent
quality problem or a missing research-relevant method, but it does not require
routine successful checks to be narrated, run research commands, or perform
those checks. It does not decide whether the science is correct. A separately
authorized correction of an identified defect is a Repair operation.

### Validate

Mechanical validation uses code to check presented computational evidence,
its declared sources, its visible command relationships, and unused retained
material. It does not run research commands, judge scientific meaning, or
perform reproduction. Semantic review and reproduction are separate workflows.
Validation reports precise problems but does not repair research content.
A later correction of a named research-owned finding is a separately
authorized Repair operation.

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

Any other label combination is structurally invalid. Research-log review owns
diagnosis and repair. Validation skips the entire invalid section, identifies
the entry and heading, and records one structural failure so the skipped
content cannot coexist with an all-clear validation result. It does not infer
the intended section type or partially validate the section.

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
./pyrun scripts/compare.py --input "<test_set>" --output-csv data/comparison.csv
```

`Results:`
The correction reduced median error from `0.292%` to `0.286%`.

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

For Python, use the entry-root `./pyrun` launcher. It uses
`<project>/.conda/bin/python` when that environment exists; otherwise it uses
the interpreter that runs `pyrun`. It also expands these path tokens:

- `<project>`: project root;
- `<log>`: research-log directory; and
- `<name>`: one exact input in the owning entry-root `data.json`; and
- `<directory-name>/member`: one exact member of a declared directory input.

Data tokens occupy the complete input argument. Quote arguments containing
angle tokens.

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
`pyrun` records each output's current script, parameters, inputs, and bytes.
When stdout or stderr is retained as evidence, use
`./pyrun --capture-stdout ... --`, `--capture-stderr ... --`, or
`--capture-stdout-stderr ... --`; raw `tee` or redirection cannot create that
confirmed output record. Output available only in an agent's temporary context
is not evidence. This is original research execution, not validation or
reproduction; do not rerun an unchanged command solely to test reproducibility
or Provenance.

### Input registry

Use entry-root `data.json` for every file or directory consumed as a material
input by a recorded command or evidence record. It contains all and only those
inputs, plus a directly presented artifact when an explicit origin boundary is
needed. Each has one stable name, local location, strong fingerprint, and
Boolean `origin`. An origin stops the Provenance chain at that artifact;
generated material continues to its unique earlier producer regardless of
where the file is stored.

Add an accessible local input from the entry folder with:

```bash
./pyrun data add development_set file /data/project/development.csv
```

`pyrun` fingerprints local content and resolves the item through
`"<development_set>"` and marks a newly added input as an origin. Raw
command-input and evidence-source paths and URIs
are invalid. Evidence sources use one complete `<name>` or
`<directory-name>/member` token and must resolve to one local regular file. A
generated output enters `data.json` when a later recorded command or evidence
record consumes it; set `origin: false` with
`./pyrun data origin <name> false` so it traces to its earlier producer. Omit
`data.json` when the entry has no command or evidence inputs and no direct
artifact origin.

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
materialize a locally accessible copy so validation can confirm its current
bytes. Provenance stops at that declared artifact; scientific review, not
mechanical validation, determines whether the source is trustworthy.

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

Every presented generated result must trace through the recorded workflow until
it reaches an explicit origin or an inputless confirmed producer. Every reached
generated output must match `pyrun`'s current output and script fingerprints,
exact ordered parameters, and direct-input fingerprints. This bounded
Provenance result does not claim causation, complete dependency capture,
scientific validity, or reproduction.

Material may also be retained intentionally for later investigation even when
it is not used by a current result. Tell the research-logging agent why it is
being kept so that it can be distinguished from an accidental orphan. This
does not turn the material into evidence.

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
links to the latest completed generated validation report. It then starts with
`## Contents`, `## Entries`, and `## Summary`. During Update Summary, add
`## Follow-ups` only when entries contain intentional `Follow-up:` items, and
add its Contents link at the same time. Every summary ends with `## AI Use`.

```md
# <Log title>

Validation: [latest completed report](<log>/validation.md)

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

### Validation link in the summary

Place this exact navigation line immediately below the level-one title,
followed by one blank line:

```md
Validation: [latest completed report](<log>/validation.md)
```

The link is stable research-document scaffolding. It contains no date, status,
failure count, artifact-currentness claim, or rules version. Do not add a `## Validation`
section or a Validation item to `## Contents`. Before the first validation, the
link may point to a report that does not yet exist.

Record, Replace, Reorganize, Repair, and Update Summary preserve this link
exactly and never edit generated validation files. Validate reads the
maintained summary but never changes it. The generated `<log>/validation.md`
report contains an independent Mechanical Validation section with its date,
scope/status counts, and non-passing checks, plus a separate Reproduction
section. Until the reproduction workflow is implemented and run, that section
states `not_yet_run`. The report has no combined conclusion.

## Reviewing a research log

Research-log review examines the record without executing its research
workflows. It normally returns numbered findings ordered by importance, with
the affected location, rule, consequence, and corrective action. Request fixes
explicitly when you want them applied.

Check the requested area against these questions:

- **Structure:** Are summary links, entry IDs, dates, folder names, citations,
  and split-entry links complete and current?
- **Section types:** Is every entry section clearly experimental, synthesis, or
  prose, with the required and permitted labels?
- **Writing:** Are evidence, observations, uncertainty, decisions,
  proposals, and validation status kept distinct? Are comparisons organized by
  question rather than run chronology? Does the entry avoid agent activity,
  routine checks, housekeeping, and task progress that do not bear on the
  research evidence?
- **Evidence:** Can each presented statistic, table, command-output excerpt,
  file, or image be traced to retained supporting material and its recorded
  workflow? Report missing, ambiguous, or stale connections, but do not decide
  whether the scientific claim is correct.
- **Reconstructing the work:** For active investigations, do recorded commands
  use the expected environment, `./pyrun`, named inputs, explicit settings, and
  saved outputs? Are scripts in the right location, figure-generation code and
  saved figures free of apparent quality problems, and later-used serialized
  files checked against their expected structure? For work completed
  elsewhere, does the record preserve the actual workflow and identify missing
  material rather than replacing it with a cleaner reconstruction? Does
  `data.json` contain all and only command and evidence inputs plus any direct
  artifact origin, use unique names and targets, resolve every token, preserve
  fingerprints, and distinguish explicit origins from generated inputs? Are intentional disconnected
  artifacts declared only through `retention.json`?
- **Summary:** Is every substantive point supported by an entry? Does the
  summary describe current understanding, preserve the stable validation-report
  link, include explicit follow-ups, and end with the AI-use disclosure?
- **References:** Do citation keys resolve to authoritative metadata in
  `refs.bib`, and are references cited where they are used?

Review checks the structure and support of the record. Validation separately
checks whether declared sources resolve, presented results match their sources,
and the computational history is complete. You retain authority over methods,
interpretation, conclusions, accepted synthesis, and research direction.

## Validating a research log

Mechanical validation is a code-only check of the recorded research log. It
does not rerun the research, judge scientific meaning, or decide whether a
result is persuasive. Those questions belong to reproduction and scientific
review.

At a high level, validation checks four things:

- the research log and its supporting metadata are structurally consistent;
- presented computational results match their declared retained sources;
- generated evidence can be traced through confirmed current output and script
  fingerprints, exact ordered parameters, and direct-input fingerprints to
  explicit origins; and
- retained files and output records are connected to the recorded work,
  intentionally kept, or reported as Hygiene findings.

These checks are intentionally strict. A missing or ambiguous relationship is
reported as a concrete finding instead of being guessed. Findings in one area
do not erase valid results in another.

### Running validation

Resolve `scripts/log` from the active research-logging skill package and name
the logical log path:

```bash
<skill>/scripts/log validate --path <log>
```

Use `<skill>/scripts/log validate --root <project-root>` for every maintained
log returned by canonical bounded discovery beneath one project.

A mechanical evaluation reports either that no findings were found or that one
or more findings need attention. A preliminary validator-state check may
instead stop before evaluation when it finds incompatible generated validation
files; this publishes no validation result. These files are not research
findings. Before validation can run, a researcher or maintainer must separately
archive them outside the active log or remove them. If the validator cannot
complete a required observation, it reports an incomplete run rather than
treating the unchecked area as valid. A reported finding is a successful
validation result, not a tool failure.

A completed published mechanical evaluation writes the human-facing
`<log>/validation.md` report. It shows when mechanical validation last
completed, summarizes the four check areas, and lists non-passing checks by
entry. Its separate Reproduction section remains independent and shows that
reproduction has not yet run until that workflow produces its own result.

The adjacent `<log>/validation/` directory contains machine-readable results.
Disposable validator caches live beneath `<log>/.cache/` and the research
project's `.cache/` directory. Entry-root `pyrun-outputs.json` is separate
current output-support state maintained by `pyrun`. Researchers and research
agents should not edit these generated files directly. Validation reads the
research record but changes only its own generated output.

### Resolving findings

Validation identifies problems; it does not repair the research record. A
separately authorized Repair operation reviews the named finding, corrects the
relevant evidence, command, source, or retained-material relationship, and then
runs validation again. When incompatible generated metadata stops evaluation
before a report is published, use the paths identified by the validation
command for a separately authorized archival or removal action; a research
operation must not modify them.

Research changes do not automatically trigger validation, semantic review,
reproduction, or summary updates. The report represents the latest completed
validation run, while the next run determines which prior checks remain current
and which must be evaluated again.
