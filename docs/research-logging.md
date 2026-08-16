# Research Logging

This document explains the research-log workflow for researchers. It describes
how to organize a log, record work, retain the material needed to reproduce
results, present evidence, keep the summary current, review the record, and
check its integrity.

The `research-logging` skill independently implements the same workflow for AI
agents. It does not read or refer to this document, and this document does not
rely on the skill for missing guidance. Their agreement is checked whenever
either one changes.

## Workflow at a glance

A research log uses five core operations:

1. **Record** research activity in a new or existing log, using numbered, dated
   entries with their supporting material.
2. **Replace** a named experimental section and its owned material when you no
   longer intend to retain the superseded work in the active log.
3. **Update Summary** when you want the current research state and follow-ups
   brought up to date.
4. **Review** structure, presentation, associations, and scientific meaning.
5. **Validate** that presented computational results are intact, traceable to
   their sources, and reproducible where applicable.

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
supporting material, `evidence.csv` records that connect results to sources,
and generated validation records. The summary describes the current state;
entries and their saved material preserve the detailed research record.

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
  evidence.csv
  validation.md
  entries/
    2026-05-01-e001-calibration-drift-check/
      e001.md
      data.csv
      pyrun -> <installed launcher>
      data/
      images/
      scripts/
      evidence.csv
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
results, source links, citations, `data.csv`, and `evidence.csv`. It preserves
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
If you already have work to record, create `e001` using the local start date
and add it to the summary's entry list. Otherwise leave `entries/` empty. Do
not create reference, script, data, image, or evidence files until they are
needed. Do not merge with or overwrite an existing log unless that is your
explicit intent.

#### Record a new investigation

Start a new entry for a distinct topic or later investigation. Use the local
start date and the next unused entry ID.

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
summary links, citations, commands, and evidence rows together.

If removal would replace experimental content, request Replace explicitly
instead.

### Replace

Use Replace only when you explicitly want a named experimental section and its
owned material to supersede work you no longer intend to retain in the active
log. The section you name may change in its `Background:`, `Steps:`,
`Results:`, and `Observations:`, together with the corresponding evidence rows
and exclusively owned scripts or artifacts needed for the replacement. Leave
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
those checks. It does not decide whether the science is correct.

### Validate

Validation checks presented computational results and the saved workflows
behind them. A standard check inspects the record without running research
code. A reproduction check reruns eligible recorded commands in a temporary
location. Validation may repeat relevant checks independently; successful
execution during Record is not validation. Validation reports problems but
does not repair research content.

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
result or use a named `data.csv` input when a command consumes it. A transformed
result belongs to the later entry that created the transformation.

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
provides contextual or connective information. Only experimental sections
enter automated validation.

Synthesis and prose sections add no validation results or `evidence.csv` rows,
and changes confined to them do not make validation out of date. A synthesis
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
| `Validation:` | When needed, direct the agent to record a narrowly defined comparison rule or explain why a named unused file is intentionally kept. Do not put validation status here. |
| `Follow-up:` | Record deferred work you want carried into the log-level follow-up list, not current planned work or speculative ideas. |

Experimental sections require `Steps:` and `Results:` and may also use
`Background:`, `Observations:`, `Decisions:`, `Uncertainty:`, `Validation:`,
and `Follow-up:`; they never use `Findings:`. Synthesis sections require
`Findings:` and may use `Background:`, `Decisions:`, `Uncertainty:`, and
`Follow-up:`. Prose sections use no labels.

Place `Validation:` after `Decisions:` when present, or after the last result,
observation, or uncertainty label, and before `Follow-up:`.

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

For Python, use the entry-root `./pyrun` launcher. It uses
`<project>/.conda/bin/python` when that environment exists; otherwise it uses
the interpreter that runs `pyrun`. It also expands these path tokens:

- `<project>`: project root;
- `<log>`: research-log directory; and
- `<name>`: the matching location in the nearest entry `data.csv`.

Tokens may be a whole argument or part of one, such as
`input=<development_set>/cases.csv`. Quote arguments containing angle tokens.

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
  --dataset "<development_set>" \
  --candidate baseline \
  --candidate trial \
  --seed 123 \
  --samples 500 \
  --summary-csv data/study-summary.csv
```

Run a new or changed script through the recorded command from the entry folder
to produce or check its saved outputs before presenting them as results. Save
command output during that run with a program log option, `tee`, or
redirection; output available only in an agent's temporary context is not
evidence. This is original research execution, not validation or reproduction;
do not rerun an unchanged command solely to test reproducibility or provenance.

### Input index

Use entry-root `data.csv` for command inputs or durable external resources that
need short names. It is not a list of scripts, images, or saved outputs.

When a new external input is needed, decide whether to copy it into the entry or
retain a stable external reference, then add the chosen location to the index.

```csv
name,type,location
development_set,CSV,/data/project/development.csv
```

The header is exactly `name,type,location`. `name` uses only ASCII letters,
digits, `.`, `_`, and `-`; it is unique and cannot be `project`, `log`, or
`theme`. `type` is a plain description such as `CSV`, `FITS`, `directory`, or
`URL`. `location` may be a path or stable remote address; relative paths start
from the entry folder.

Add a row from the entry folder with:

```bash
./pyrun data add development_set CSV /data/project/development.csv
```

The command creates the header when needed and rejects malformed rows,
duplicate names, and reserved names. It does not copy or inspect the referenced
resource. Then use `"<development_set>"` in the recorded command. Add only rows
that a recorded command uses. A generated output belongs in `data.csv` only
when a later recorded command consumes it as an input.

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

### External inputs and references

For an external workflow input, record what it is and how it was used. Keep a
copy when practical; otherwise record a stable reference. Validation checks
that the input is identified, not that the external source is scientifically
trustworthy.

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

Computational evidence is treated as a presented result when it appears in one
of four recognizable forms:

- an explicitly presented saved file or collection, including structured data,
  textual output, or a figure;
- a Markdown table;
- a `text` code block containing an excerpt from saved command output; or
- an explicitly marked numerical statistic, including a derived comparison.

A link to a saved file, an embedded image, a Markdown table, or an excerpt of
saved command output in a `text` code block is presented evidence only under
`Results:`. Tables elsewhere remain available for non-evidential information.
A linked or embedded file must be identifiable either as a `data.csv` resource
resolved by a recorded command or as an output path resolved from a value in a
recorded command or a file written with `tee` or redirection. The value's role
follows from command and code context; it does not require a particular
parameter name. Merely naming a file in prose does not present it directly.

Prefer tables, figures, and marked statistics to direct links to output files.
Link an output file only when direct inspection or reuse of the file is itself
important; do not list output files merely to report that a command created
them.

A numerical statistic in an experimental entry section or summary is presented
evidence when its value and units are enclosed in inline code formatting. Keep
the quantity name and connective comparison wording outside the formatting:
write “Error fell from `0.292%` to `0.286%`.” A derived comparison such as
`14.3% lower` is one evidence item only when that derived value exists in a
saved output. Inline code that clearly contains text, an identifier, or a
parameter is not evidence. Numerical content that is not evidence must avoid
inline code formatting when it could be mistaken for a marked result. Unmarked
numerical prose is not checked. A Markdown table is one presented item, not a
collection of statistics: use plain formatting for numerical cells and reserve
backticks in tables for visibly textual identifiers or code labels. Formatting
inside synthesis and prose sections has no validation meaning.

If you discuss a table value again as a numerical claim in experimental prose,
mark and index that prose claim separately; the table does not stand in for it.

Presented results and supporting files have a many-to-many relationship. One
file may support several results, and one result may draw on several files. A
table does not need a separate file for every cell. Each marked statistic,
Markdown table, and block showing saved command output has one row in the entry
folder's `evidence.csv`, which records its saved source or sources. File links,
image embeds, and collections do not use this record because they must connect
directly to their recorded workflow.

An association must establish that the source and presented result have the
same meaning in context; finding the same numeral in an unrelated field is not
sufficient. Rounding, reformatting, equivalent numeric notation, and lossless
selection or ordering are permitted. A derived result must itself come from a
saved workflow output rather than an ad hoc calculation made while drafting.

For structured files, a locator may select particular rows, fields, arrays,
datasets, or properties such as shape and size. Validation reads only the
needed portion. It does not open pickle files as evidence; retain a CSV or JSON
summary from an explicit command instead.

Every presented project-generated item must trace to saved output produced
by an identified workflow and through its code, configuration, direct inputs,
and upstream generated inputs until it reaches the original source inputs.

### Connecting results to source files

`data.csv` indexes command inputs; `evidence.csv` maps presented evidence to
its immediate saved sources.

An entry folder uses `evidence.csv` when an experimental section presents at
least one marked statistic, Markdown table, or block showing saved command
output. Use one row per presented item and this exact header:

```csv
entry,section,kind,evidence,sources,transformation
```

`entry` is the owning entry document ID and distinguishes documents in a split
entry folder. `section` is the exact preceding Markdown heading. `kind` is
`statistic`, `table`, or `output`. `evidence` is a short description copied
from the presented content: use the marked numerical expression for a
statistic, the ordered column headings for a table, or the first distinctive
non-empty line of saved command output. Add minimal surrounding wording or an
occurrence number only when otherwise identical items occur in the same
section.

`sources` names the immediate saved source or sources. Use paths relative to
the entry folder, `<log>/...` for material in the log directory, or an exact
`<name>` token from the entry's `data.csv`. Do not place absolute paths, web addresses, or
remote-storage addresses directly in this record. Separate several sources with
` | ` and separate a source from its optional locator with ` :: `. A statistic
names exactly one saved source file, an output block names exactly one saved
command log, and a table names one or more saved source files.

Omit the locator only when the whole file clearly supports the result. Otherwise
add a stable source selector, called a locator, that identifies the relevant
part of the file. Prefer row keys and fields, structured key paths, datasets,
records, or distinctive text over line numbers. Use `transformation` only when
needed to describe selection, ordering, table assembly, rounding, or equivalent
formatting. It may not describe a new calculation; a derived result must
already exist in its saved source.

Use this closed locator format after ` :: `:

- For CSV, TSV, and other tables, filter exact values with `column=value`,
  separate filters with `; `, and select the result with `field=name` or
  `fields=name|name`. Use `value1|value2` for alternatives in one filter. If a
  source column is itself named `field`, `fields`, `path`, `property`, or
  `text`, prefix the filter with `where.`.
- For JSON, NPZ, HDF5, and similar structured files, use `path=`. Separate keys
  with `.`, use `[n]` for an item, `[start:stop]` for a slice, and `path=$` for
  the file root. Add filters and `field=` or `fields=` when the selected object
  contains records or aligned arrays. HDF5 dataset paths may use `/`. Use
  `property=shape`,
  `property=shape[n]`, or `property=size` only when the presented result is
  structural information about an array or dataset.
- For text and command logs, use `text=` followed by a distinctive literal
  fragment.

Use `; ` only between clauses and `|` only between alternative exact values or
field names. Do not put either separator inside a value; choose a different
stable key or text fragment. Commas are literal data, although the surrounding
CSV cell still follows normal CSV quoting.

Examples:

```text
data/comparison.csv :: case=baseline; field=error_percent
data/comparison.csv :: case_id=8|15; fields=case_id|error_percent
data/comparison.csv :: where.field=validation_error; candidate=trial; field=difference
data/results.json :: path=simulation[0].throughput_pix_per_s
data/run.npz :: path=$; labels=base; field=wind_delta_deg
<training_pool> :: path=status/state; property=shape[0]
data/run.log :: text=completed 49152 outer pixels
```

A complete entry record can then use those locators:

```csv
entry,section,kind,evidence,sources,transformation
e004,Model comparison,statistic,14.3% lower,"data/comparison.csv :: row=standardized; field=mse_reduction_percent",
e004,Model comparison,table,"model,mse,relative change","data/comparison.csv :: row=baseline|standardized; fields=model|mse|relative_change","Rows selected and values rounded to three decimals"
```

The `evidence.csv` in the log directory maps every presented summary statistic
to exactly one supporting entry and section. `statistic` uses the same rule for
choosing identifying text as an entry statistic. Use this exact header:

```csv
statistic,entry,section,transformation
```

For example:

```csv
statistic,entry,section,transformation
14% lower,e004,Model comparison,Rounded from 14.3% to a whole percentage
```

Update an evidence row whenever its presented item, identifying text, section,
source, locator, or transformation changes. Record and Replace maintain entry
rows with their presented evidence; Replace remains within its approved scope.
Update Summary maintains log-level rows with presented summary statistics.

When values change inside the same source but the presented item, section,
source, locator, and transformation remain correct, leave the evidence row
unchanged. Validation detects the changed source when it next runs.

Reorganize may repair only an `entry` or `section` value in a log-level row
made stale by an approved move or heading change. It does not change the
statistic, transformation, summary wording, or set of summary evidence rows.

These records are part of the research record, not validation results. Review
reports problems and Validate reads and verifies rows; neither changes them. A
requested repair uses Record or Replace for an entry row and Update Summary for
a log-level row. Delete a header-only file after removing its last required
row. Report any result-to-source link that cannot be established confidently
rather than guessing. Missing, malformed, ambiguous, out-of-date, or incorrect
required rows fail the source-tracing check called Provenance. Validation
reports the issue rather than repairing it.

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
in one experimental entry section. The `evidence.csv` in the log directory
records this link. Research-log review determines whether qualitative summary
points are supported by entries. Summaries do not contain tables, images,
blocks showing saved command output, or links to saved files.

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
failure count, freshness claim, or rules version. Do not add a `## Validation`
section or a Validation item to `## Contents`. Before the first validation, the
link may point to a report that does not yet exist.

Record, Replace, Reorganize, and Update Summary preserve this link exactly and
never edit generated validation files. Validate reads the maintained summary
but never changes it. The generated `<log>/validation.md` report owns the date,
Summary status, included entry set, Integrity and Provenance counts,
Reproducibility status, and current failures.

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
- **Evidence:** Does each presented statistic, table, or command-output excerpt
  have exactly one valid `evidence.csv` row? Are file links and images connected
  to recorded commands? Report missing, duplicate, extra, malformed, or
  out-of-date rows, but do not decide whether the scientific claim is correct.
- **Reconstructing the work:** For active investigations, do recorded commands
  use the expected environment, `./pyrun`, named inputs, explicit settings, and
  saved outputs? Are scripts in the right location, figure-generation code and
  saved figures free of apparent quality problems, and later-used serialized
  files checked against their expected structure? For work completed
  elsewhere, does the record preserve the actual workflow and identify missing
  material rather than replacing it with a cleaner reconstruction? Does
  `data.csv` avoid duplicate or unused names, unresolved tokens, and script or
  image rows?
- **Summary:** Is every substantive point supported by an entry? Does the
  summary describe current understanding, preserve the dated validation
  record, include explicit follow-ups, and end with the AI-use disclosure?
- **References:** Do citation keys resolve to authoritative metadata in
  `refs.bib`, and are references cited where they are used?

Review checks the structure and support of the record. Validation separately
checks whether declared sources resolve, presented results match their sources,
and the computational history is complete. You retain authority over methods,
interpretation, conclusions, accepted synthesis, and research direction.

## Validating a research log

### What validation checks

Validation checks whether deliberately presented computational results are
intact and traceable to saved source material. It asks three questions:

- Does each saved result exist and open correctly?
- Can each presented result be traced through the recorded command, code,
  settings, and inputs that produced it? This history is called provenance.
- When reproduction is requested, can the recorded workflow regenerate the
  result?

Validation recognizes evidence from the formats defined in Results and
supporting evidence. It does not treat every file, number, command, or citation
as evidence. It also does not judge whether a scientific method, interpretation,
or conclusion is correct.

### How validation works

Checks use automated inspection wherever the answer is clear. When a
relationship cannot be established safely from file paths, commands, and file
contents alone, an independent validation agent reviews the relevant material.
Ambiguity is reported; it is not treated as success.

A missing or malformed required evidence record remains a failure for that
run; interpretation cannot override it. For a marked statistic, finding the
same number in the selected source is only a candidate match. The selected
value must support the same claim in context before Provenance can pass.

Validation follows each result back through the materials needed to produce
it. Shared inputs and code are checked once and their results are reused where
appropriate. An explicit path into another log is external evidence of the
log being checked: the validator observes that path directly without reading
the other log's validation files or requiring that log to be initialized or
current. Unused-material status remains local. A file unconnected within its
own log is still a local validation problem even when another log refers to it.

A standard check inspects the saved record without running research code. A
reproduction check reruns eligible recorded commands in a temporary location
and compares the new outputs with the saved results. The validation agent reads
`evidence.csv` but never changes it.

### Standard and reproduction checks

Canonical validation covers one maintained log and is the only form that
updates generated validation records. It never updates the maintained summary
or coordinates validation of the other maintained logs.

A standard check examines presented results, saved files, commands, code, and
inputs without executing the research workflow. Earlier results may be reused
when the relevant material and validation rules have not changed. If something
has changed, only the affected checks are repeated. A complete run still
reports every discovered problem in the log; a correct `FAIL` result means the
check itself completed successfully.

A reproduction check reruns eligible commands in a temporary location and
compares each regenerated file with the saved result. It never overwrites
saved research material. Slow or expensive work—such as large simulations,
data processing, or model training—is excluded unless you request it
explicitly. A canonical reproduction still covers the complete log inventory,
while rerunning only the eligible workflows included in that reproduction.
Temporary outputs are removed after comparison.

By default, reproduced outputs must be logically identical:

- text and opaque files match byte for byte;
- figures match as decoded images;
- CSV, JSON, FITS, HDF5, and similar structured files match in structure and
  values.

An experimental section may include a short `Validation:` note when a result
needs a different comparison rule or when an otherwise unused file is being
kept intentionally. Name the exact file or directory, state what may differ,
what must still match, and why. The validation agent may follow these notes but
cannot create or relax them. Only you, or a research agent acting on your
direction, may change them.

Place `Validation:` after `Decisions:` when present, or after the last result,
interpretation, or uncertainty label otherwise, and before `Follow-up:`.

### What gets checked

Validation reports results by entry document and stable entry ID. Split entry
documents such as `e002a` and `e002b` remain separate.

Each saved file or collection supporting a presented result receives one row
in `validation.md`. Several presented results may point to the same row through
`evidence.csv`, and one result may depend on several rows. Commands, scripts,
settings, and inputs are checked as part of the saved result they produced,
not as separate scientific results.

A direct external input that a recorded command only consumes is a terminal
source. It needs a stable identity and its actual use recorded, but it does not
need an invented producing command.

When a generated input or result has several eligible recorded producers,
validation must select the one exact command invocation that produced it. It
does not combine alternative workflows. A change to that command or to the set
of eligible alternatives reopens the affected Provenance result.

A directory or dataset collection may use one row when it is presented as a
single result. Members presented separately receive separate rows. A Markdown
table or block showing saved command output remains one presented result rather
than one result per cell or line.

If a marked statistic, table, or block showing saved command output has no
valid `evidence.csv` row, validation adds a failed row identifying the
unsupported result. If a declared source file is missing, its normal row fails
both Integrity and Provenance. Synthesis and prose sections create no evidence
rows. An invalid section creates one structural failure rather than partial
results for its contents.

The separate Summary table contains one row per marked statistic in the
current summary. Each statistic must point to exactly one entry and one section
and must not introduce a new calculation.

Validation also looks for research material that is saved in the log but is
not connected to a presented result. This includes scripts, files, and
`data.csv` resources used by recorded commands. Validation reports such items
as unexplained or unused material; the report uses the established label
`Orphaned artifacts, scripts, and references`. This status is local to the log:
use by another maintained research log does not connect the material to a
presented result in its own log.

One catch-all row is used for each affected entry folder or for the log as a
whole. The row uses `-` for Section, `N/A` for Integrity and Reproducibility,
`FAIL` for Provenance, and gives the number of unresolved items in Notes. The
individual paths appear in `validation.md` under `## Remediation`. A `Validation:` note you
approve can explain why a named item is intentionally kept.

When semantic review is needed, validation emits a bounded packet containing
only the unresolved questions and evidence needed for those decisions. The
validator owns candidate selection and continuation; the agent fills only the
requested decision and concise rationale fields. A decision bound to changed
evidence or an older continuation is rejected without changing the durable
record.

Bounded review may also establish that an apparently unused item participates
in presented work through a relationship that paths and commands could not
show mechanically. This is different from a retention exception: the former
records an evidence connection found during validation, while the latter
records your explicit decision to keep an otherwise unused item.

For residual material under an entry's `data/`, `images/`, or `scripts/`
tree, validation may ask whether a proposed subtree has one lifecycle or must
be split. A classification applies prospectively to compatible descendants;
splitting returns immediate child folders and loose files in the next bounded
round. The material directories themselves are containers, never subtree
units: questions begin at their immediate subfolders, while loose files use
exact-path review. Graph connections take precedence, followed by exact-path
exceptions and then the most-specific compatible subtree rule.

### Check results

Every saved result uses three peer checks:

| Check | Successful result means |
| --- | --- |
| Integrity | The saved result exists, can be opened safely, and has the expected structure. Figures render and collections contain their expected members. A known unsupported or prohibited format remains unresolved unless it is explicitly treated as an opaque file. |
| Provenance | The presented result matches its declared source and can be traced through the recorded command, code, settings, and inputs. |
| Reproducibility | The recorded workflow regenerated the result and the new output matched the saved result under the stated comparison rule. |

External or compiled software can pass Provenance without inspecting its
internal code when the exact software and version or build are identified and
the recorded command names it. Note this limitation in Notes. Missing or
ambiguous software version fails Provenance.

A temporary access failure for an otherwise well-identified external source is
not itself a validation failure. Note the access boundary. A missing required
input description, saved copy, or stable source reference fails Provenance.

A successful check records its most recent success date. An unsuccessful check
reports `FAIL`. Reproducibility uses `-` when it has no current result and
`N/A` when it does not meaningfully apply. Standard validation does not replace
`-` with a date. Requested reproduction reruns the eligible workflows selected
for that run; canonical reproduction retains complete-log scope.

If material used by a prior reproduction result changes and reproduction is not
requested, replace its date with `-`; do not report `FAIL` because reproduction
was not attempted. Only `FAIL`, `-`, and `N/A` use inline code formatting in
generated report cells. Successful dates and results use ordinary text.

Before reusing an earlier result, a standard check observes the dependencies
recorded for that outcome. New, renamed, missing, inaccessible, ambiguous, or
changing material remains explicit. Size, modification time, and change time
are inexpensive change-detection evidence: when all three are unchanged, the
validator reuses the stored content identity without opening the file. When
metadata changes, one shared hash distinguishes a byte-identical rewrite from
a content change. Only outcomes that depend on changed content or rules reopen.

### Validation report and generated files

`<log>/validation.md` is the official researcher-facing record of completed
validation results. It assembles dependency-bound observations, preserves each
outcome's result date, and records whether the operation used standard or
reproduction validation. It does not claim one repository-wide or whole-log
snapshot and does not reduce the log to one overall pass-or-fail result.

Near the top, `## Status Summary` gives the report-update date, Summary status,
and one compact row per included entry. Detailed sections cover marked Summary
statistics and every checked entry target. Counts show how many rows were
checked and how many failed. Short notes may identify the presented result,
explain an approved comparison rule, or state a known inspection limitation.
Detailed problems belong under `## Remediation` in the same report.

#### Details about problems

The report's generated `## Remediation` section groups problems by summary or
entry and identifies which check failed, what was found, and any specific
unresolved paths or `data.csv` references. It is part of the durable completed
record. A research agent resolves or disputes a finding by changing
research-owned evidence or instructions; the next completed validation alone
rebuilds the report.

Validation also maintains machine-readable state:

- `<log>/validation/manifest.json` is the small authoritative manifest. It
  owns the project-relative summary identity, current stable continuation,
  completed-report projection, and exact outcome, judgment, and failure shard
  references.
- `<log>/validation/{outcomes,judgments,failures}/` contains immutable
  content-addressed row shards. Semantic judgments and rationales, completed
  outcomes and dates, observed dependencies, and failures have no other
  machine-readable owner. Completion never recombines these rows into a
  monolithic JSON record.
- `<log>/validation/.cache/` contains rebuildable or transient local state:
  the deterministic evidence cache, stable-subject index and batch deltas,
  active review sessions, and validation lock. None has independent
  correctness authority.

At terminal completion, exact orphan judgments superseded by active compatible
subtree rules are removed from the new manifest; exact exceptions and
unrelated history remain. Replacement shards are written before the manifest,
and old unreferenced shards are deleted only after the new manifest and report
are coherent. `--dry-run` reports this cleanup without writing it.

Commit `validation.md` and the complete durable `validation/` closure except
`validation/.cache/`. Ignore `**/validation/.cache/`; a missing cache or index
only triggers bounded reconstruction from the authoritative manifest and row
shards.

Research agents preserve these files exactly; validation agents create,
update, or remove them through the validation tool. Only `validation.md` is
intended for direct use as a validation record. Temporary semantic review
packets and decision templates live under the owning log's ignored
`validation/.cache/work/` directory and are not canonical validation
artifacts. A paged review can resume from the maintained-summary path alone;
the manifest names the stable session while its small state file owns the
current page and accepted batches.

Compatible reused outcomes keep their original result dates, so a current
report can legitimately contain dates older than its report-update date.

## Roles and keeping validation current

Validation is performed in a fresh agent task that did not produce the work
being checked. This separates the check from the earlier work session, but it
is not a claim of organizational independence or independent scientific
replication.

Research changes and canonical validation are separate activities. Validation
is observational: completed outcomes record the dependencies actually observed
for those checks, while an input that changes during observation remains
unresolved. Unrelated concurrent changes do not discard compatible completed
work. Canonical writers serialize per log, so different logs may validate and
publish concurrently while two writers to the same log cannot compete.

The validation agent may read maintained summaries, entries, scripts, saved
evidence, `data.csv`, `evidence.csv`, and authored `Validation:` notes, but it
cannot change them. It records results only in generated validation files and
reports problems without repairing the research record.

The research agent may edit the research record, maintain `evidence.csv`, and,
under your direction, add or revise an authored `Validation:` note. It never
edits, deletes, repairs, or normalizes generated validation files and cannot
assign a completed validation result.

Research changes do not trigger validation, reproduction, broad checks, or a
summary update. Perform only the production check needed to keep changed
presentation consistent with its retained source. The next Validate request
compares outcome dependencies with saved observations, reuses compatible work,
and publishes only the validation report, durable record, and rebuildable
cache. Missing or changing evidence affects only the outcomes that depend on
it. Validation does not inspect or report version-control status.
