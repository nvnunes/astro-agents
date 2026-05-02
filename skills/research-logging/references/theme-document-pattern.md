# Theme Document Pattern

## Overview

This document defines a reusable research-log hierarchy for project-native theme areas.

It is the current concrete pattern inside the research-logging skill, not a project-specific research log.

For routine maintenance, use `skills/research-logging/references/theme-routing.md`. This document remains the fuller concept and design reference.

The pattern should remain flexible because researchers and projects organize work differently. A research-log hierarchy does not need one fixed top-level location. It can live wherever a project already expects a theme's documentation to live.

User-facing language can vary. Phrases such as theme structure, theme log, theme notes, theme docs, research theme, theme area, or research thread may all refer to this same theme-document pattern when the context fits.

Theme and concept slugs should be enclosed in backticks when written in theme files. Avoid duplicate slugs within the same theme.

The basic shape is:

```text
<theme>.md
<theme>/index.md
<theme>/entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/index.md
```

The theme document is a first-class living summary. The theme folder carries the timeline index, dated evidence entries, and entry-specific artifacts.

The theme-document pattern's contribution is not the folder location itself. Its contribution is how the hierarchy is implemented so agents can retrieve context efficiently, distinguish the living summary from historical evidence, preserve provenance, and update summaries without loading everything.

## Human-Agent Lifecycle

Theme-document maintenance should balance automation with explicit researcher direction.

There are three core operations. These are agent-facing distinctions, not terms the researcher must use:

- `Capture`: record what happened. This is the default operation. It updates entries and the timeline index, but normally does not rewrite the living summary.
- `Update`: update `<theme>.md` from a specific entry or clearly identified entry changes.
- `Check`: review `<theme>.md` against the log and report issues before applying fixes.

The researcher should be able to use ordinary language. The agent should infer the operation or operations needed, choose the safest reasonable interpretation, and avoid asking the user to manage the mechanics unless the request is ambiguous.

`Capture` should feel especially fluid. The agent should keep the recommendations in mind, lightly suggest or use them when helpful, and avoid forcing structure into the conversation while the researcher is still working through material.

The default bias is to preserve evidence first and defer summary updates until the user asks or a specific entry clearly changes current understanding. This avoids prematurely stabilizing claims while research is still evolving.

Deferred summary updates should remain efficient. Capture work should leave behind enough routing information in `<theme>/index.md` for a later agent to identify relevant entries without rereading the full archive.

## Instruction Taxonomy

The reusable theme-document guidance is organized as a small routed instruction surface.

- `skills/research-logging/references/theme-routing.md`: routing entrypoint. It identifies the needed operation, applies global safety rules, and points to narrower instruction files.
- `skills/research-logging/references/file-*.md`: file-writing guides. These explain how to create or revise specific research-log document types.
- `skills/research-logging/references/operation-*.md`: operation guides. These explain how to perform work such as capture, summary updates, summary checks, source-document upgrades, or concept management.

Expansion should preserve this taxonomy unless there is a clear reason not to.

- Put concept, rationale, and taxonomy in `skills/research-logging/references/theme-document-pattern.md`.
- Put routing, operation selection, and global safety gates in `skills/research-logging/references/theme-routing.md`.
- Put reusable operational detail in research-log skill `references/` files.
- Prefer `file-` and `operation-` prefixes for new instruction files.

## Source Document Upgrade

Upgrading an existing source document into a theme hierarchy should be a human-agent loop, not an automatic rewrite.

Infer the theme name from the source document's file stem unless the user provides a different theme name.

Use this interaction pattern:

1. Agent reviews the source document.
2. If the document is section-rich enough to support either approach, the agent asks whether to prefer more entries with fewer sections or fewer entries with more sections.
3. Agent proposes a plan, including whether optional human-guided entry-by-entry retrospective `AI Use:` review is recommended after the upgrade.
4. Human adjusts and approves the plan.
5. Agent performs the split and drafts the summary.
6. Human adjusts and approves the summary.
7. If approved in the plan, the agent performs the retrospective `AI Use:` review as a human-guided entry-by-entry process.

The proposed plan should include:

- concepts and sub-concepts, preferring less depth unless the human asks for more
- entries to create
- proposed entry paths or filename components for each entry
- files to move into entries
- proposed `AI Use:` labels raised by the source document
- whether optional human-guided entry-by-entry retrospective `AI Use:` review is recommended after the upgrade
- summary content already present in the source document

During the split, entry shards must preserve the original content and source wording. Labels may be added, and very light reorganization is acceptable when it improves clarity or retrieval, but the split should not silently rewrite or paraphrase the source evidence. Nothing should be omitted, condensed away, paraphrased, or materially rewritten without the user's explicit approval.

Retrospective AI-use labeling is a provenance judgment, not a mechanical formatting step. Add `AI Use:` labels only when the human approves them.

The first generated `<theme>.md` is an initial living summary. It should be good enough to review and navigate, but it is expected to improve through direct user edits, prompted edits, and later entry-driven updates.

Summary-like material in the source document must not be dropped just because it does not fit an entry. The agent should classify it as one of:

- evidence-local interpretation that belongs in an entry
- current understanding that should be preserved in `<theme>.md`
- ambiguous material that needs human direction

## Layer Roles

Use three layers for each theme area.

- `<theme>.md`: first-class living summary. This is where retained conclusions, current understanding, accepted decisions, unresolved questions, and links to the most important evidence land.
- `<theme>/index.md`: timeline index. This lists dated entries in order with short annotations, status, major findings, and links.
- `<theme>/entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/index.md`: evidence record. This keeps detailed dated material, commands, results, artifacts, scripts, limitations, and local context.

Boundary rule:

- `<theme>.md`: what do we currently believe, and why?
- `<theme>/index.md`: what happened when, and where is it recorded?
- `entries/.../index.md`: what exactly was done, observed, produced, and decided at that point?

Audience orientation:

- `<theme>.md` is human-first, agent-second.
- `<theme>/index.md` is agent-first, human-second.
- `<theme>/entries/.../index.md` is human-first, agent-second.

Style implication:

- Use labeled-line metadata in the agent-first index when it improves routing.
- Do not make labeled lines the standard structure for human-first documents.
- Human-first documents should read like research documents, notes, or reports, with structure chosen to fit the material.

## Theme Summary

The `<theme>.md` file should be self-contained enough to explain the current state of the theme without requiring the reader or agent to open every dated entry.

It should follow the project-documentation writing discipline in `skills/project-docs-writing/references/project-docs.md`: direct, compressed, scannable, and explicit about current understanding. Detailed evidence, lengthy caveats, historical steps, and reconstruction detail belong in entries, with clickable entry-ID links from the summary where the reader may need support.

It is a living document that must be maintained. Treat user edits, ordering, prose, emphasis, and framing as intentional. Agent edits should normally be targeted updates from a specific entry, clearly identified entry changes, or direct user requests to revise summary text, not broad rewrites.

Summary maintenance is bidirectional:

- Entry-to-summary: when an entry changes current understanding, update the affected summary section.
- Summary-to-entry: when the user asks to change the summary in a way that may disagree with supporting entries, check the relevant entries, warn the user about the inconsistency, and ask whether to update the entry, revise the summary change, or leave the inconsistency noted.

It should also be navigable into the detailed log. Use clickable entry-ID links for key claims that a reader may want to inspect in the entries. Link retained conclusions, decisions, major caveats, current-versus-historical status markers, and follow-up items. Do not require every sentence or observation to carry a link.

Resolve entry paths through `<theme>/index.md`. When `<theme>.md` sits next to `<theme>/`, link to `<theme>/<entry Path from index>index.md`. For example, if `Path:` is `entries/2026-04-21-runtime-e006-dynamic-scheduler-runtime-validation/`, link from `benchmarking.md` as `[e006](benchmarking/entries/2026-04-21-runtime-e006-dynamic-scheduler-runtime-validation/index.md)`.

Use a simple high-level structure:

- `Summary`: the current understanding.
- `Next Steps`: theme-level future work, phases, priorities, and broader planned directions.

Organize the `Summary` around concepts, not entries. A concept section may draw from many entries, and one entry may inform several concept sections.

Allow arbitrary nesting under the summary. Non-leaf sections organize the researcher's mental model. Leaf sections carry the epistemic summary.

Unless direct user instruction specifies a different structure, use these available leaf-section categories:

- `Observations`: what was seen, measured, noticed, or established from the entries.
- `Conclusions`: what is currently inferred or retained from those observations.
- `Questions`: what remains unresolved, ambiguous, or worth testing.
- `Decisions`: what has been adopted, rejected, deferred, or operationalized for this concept.
- `Follow-Up`: local task-oriented work implied by this concept.

Use only the categories that carry useful current content for that concept. Do not create headings just to complete a pattern, and do not manufacture placeholder questions, generic future-update instructions, or maintenance reminders. Omit `Questions`, `Decisions`, or `Follow-Up` when there is no substantive content for them.

Formatting guidance:

- `Observations` should be format-flexible. Use prose, bullets, tables, or short subsections as needed to fit the information.
- `Conclusions` should usually use an unordered list.
- `Questions` should usually use an unordered list.
- `Decisions` should usually use an unordered list.
- `Follow-Up` should usually use a numbered list because it is task-oriented.
- `Next Steps` should use a numbered list, with nested unordered subpoints when a step needs supporting context.
- Use inline linked entry IDs, such as `[e004](benchmarking/entries/2026-04-18-prediction-e004-device-shape-control-and-dense-field-policy/index.md)`, rather than bare entry IDs when linking summary claims to evidence.

These categories encourage scientific discipline by separating observation from inference and preserving uncertainty where claims are still unsettled. User-shaped summary structure may change the layout, but should preserve these distinctions unless the researcher explicitly chooses otherwise.

Example:

```text
Summary
  Concept A
    Sub-Concept A1
      Observations
      Conclusions
      Decisions
    Sub-Concept A2
      Observations
      Conclusions
      Questions
  Concept B
    Observations
    Conclusions
    Decisions
    Follow-Up
Next Steps
```

`Follow-Up` is a current view of local task-oriented work for a leaf concept. It should normally be derived from `Follow-up:` items recorded in dated entries, not treated as the source record for why the work exists.

`Next Steps` is theme-level and may include future phases, sequencing, priorities, or broader planned investigations.

## Retrieval Surfaces

The theme hierarchy should make progressive context loading practical. It should give agents cheap ways to narrow the search space before opening detailed evidence.

Use the retrieval surfaces for different jobs:

- `entry folder names`: filesystem-level routing. Folder names should help an agent identify likely relevant entries from a directory listing before opening files.
- `<theme>/index.md`: low-context triage. The index explains what entries record, why they matter, how they relate to concepts, and which entries should be opened or skipped.
- entry `##` sections: intra-entry routing. Section headings let an agent scan a large entry before reading the full file.
- entry callouts: local retrieval. Callouts make specific inputs, code, commands, outputs, observations, decisions, follow-up, and AI use easier to find inside a section or entry.
- `entries/.../index.md`: detailed record. Entry files should be opened only after folder names, the timeline index, and section headings indicate relevance.

This means folder names, `<theme>/index.md`, entry sections, and callouts intentionally overlap, but not at the same depth. Folder names provide coarse routing. The index provides semantic routing. Section headings provide intra-entry routing. Callouts provide local retrieval. Entries preserve the detailed record.

## Timeline Index

The `<theme>/index.md` file is an agent triage surface, not another current-state summary.

Its job is to help a human or agent decide which dated entries to open, and which entries to skip, before spending context on detailed evidence.

Design goals:

- Keep the index short enough that reading it does not create substantial context inflation.
- Include enough routing information that agents do not miss important next-hop entries.
- Reduce unnecessary entry visits by explaining when an entry is probably not relevant.
- Preserve chronological navigation without forcing the document into a rigid table if a table is too compressed.
- Describe entry contents for retrieval, not to restate the full conclusions or evidence.

The index should optimize the tradeoff between missing relevant evidence and loading unnecessary documents.

### Index Shape

Use annotated timeline entries rather than a rigid table.

Use this document shape:

```md
# <Theme> Timeline Index

## Concepts

...

## Entries

### <start-date> - <Topic>

ID: `<entry-id>` where `<entry-id>` uses `e###`, such as `e001`
Path: `entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/`
Status: `<status>`
Summary: ...
```

The top of `<theme>/index.md` should enumerate the current concepts for that theme. Concepts may be organized however the living summary needs: top-level areas, sub-concepts, nested sections, or another user-shaped structure. A concept slug should be shown for any concept used as a folder-name routing slug. Concept slugs should be enclosed in backticks and should not be duplicated within the same theme.

The `Summary` in `<theme>.md` should be derived from this concept list. The list tells the agent what summary organization is expected without forcing sub-concepts into the ID structure.

If the user changes the concept organization here, the theme summary should be reorganized to match. Entry IDs do not encode concept organization and should not change when concepts are reorganized.

Entry IDs are stable theme-local identifiers in `e###` form, such as `e001`, `e002`, and `e003`. The `e` prefix means entry. Do not substitute a prefix derived from a theme, concept, status, version, or user term. Put that meaning in the concept slug, descriptive topic slug, status, or summary instead.

Example:

```md
## Concepts

- Runtime baseline (`runtime`)
  - Full-sky runtime (`full-sky`)
  - Worker scaling
  - Traversal baseline
- Storage layout (`storage`)
  - Zarr layout
  - Map staging
- Cache behavior (`cache`)
- Metrics and measurement methodology (`metrics`)
```

Entry folder names should include a concept slug, stable entry ID, and descriptive topic slug:

```text
entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/
```

Example:

```text
entries/2026-04-19-runtime-e001-full-sky-runtime-worker-scaling/
```

The concept slug is human-readable filesystem metadata for directory scanning. It may refer to a top-level concept, sub-concept, method, artifact type, or any other short label that helps locate likely relevant entries before opening files.

The agent chooses the descriptive topic slug when creating the entry. It should be concrete enough for filesystem scanning, but should not try to summarize the whole entry. If the user dislikes the topic slug, they can ask for a folder rename.

Use `<theme>/index.md` as the primary resolver from entry IDs to paths.

Reference economy:

- `<theme>.md` should use clickable entry-ID links for key claims.
- Entry-to-entry references should use entry IDs.
- Supersession references should use entry IDs.
- Plain prose paths should appear in `<theme>/index.md`, not repeatedly throughout the theme. Markdown link targets in `<theme>.md` may include entry paths so linked entry IDs are clickable.

This keeps ordinary references compact while preserving a short route from each index card to the next file.

Recommended card format:

```md
### <start-date> - <Topic>

ID: `<entry-id>` where `<entry-id>` uses `e###`, such as `e001`
Path: `entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/`
Status: `<status>` by `<superseding-entry>` when relevant
Summary: Short high-level description of what the entry records and why it may matter. Key content: important content type or topic; important artifact, result, method, or decision area.
```

Status vocabulary should stay simple:

- `current`
- `superseded`
- `historical`
- `exploratory`
- `rejected`
- `provisional`

When an entry is superseded, record what superseded it when that is known:

```md
Status: `superseded` by `e004`
```

Ordering and grouping:

- List entries chronologically from oldest to newest.
- Keep superseded and historical entries in chronological position.
- Do not add tags by default.
- Mention secondary concepts in the `Summary` only when useful.

Folder renames are path maintenance. If the date, concept slug, or descriptive topic slug in an entry folder changes, keep the same entry ID, update the entry's `Path:` in `<theme>/index.md`, update any direct path references, update clickable entry links in `<theme>.md`, and search for stale old paths before finishing.

## Entries

Dated/topic entries should preserve source evidence with enough detail for later reconstruction.

Entry files are human-first, agent-second. They should not use labeled-line metadata as their standard structure.

For the canonical entry file shape, headings, subentry behavior, callouts, and command conventions, use `skills/research-logging/references/file-entry.md`.

Let each entry follow the material. Use prose, tables, command snippets, plots, copied notes, dated sections, or other structures that fit the evidence.

Prefer standard callout labels when they make key information easier to find, but treat these as suggestions rather than required fields.

A useful flow is:

```text
Related -> conceptual description -> Input -> Config -> Code/Command -> Output -> Observation -> Limitation -> Question -> Decision -> Follow-up
```

Preferred callouts:

- `Related:` for entry IDs connected to the current note, with theme context when the reference crosses themes.
- `Input:` for source artifacts, datasets, configs, build lineage, sample files, or map files.
- `Config:` for settings, parameters, sample, run window, model assumptions, or comparison scenario.
- `Code:` for changed notebooks, scripts, code locations, or commits materially involved in the entry.
- `Command:` for commands used to regenerate plots, run benchmarks, or reproduce processes.
- `Output:` for external generated files retained by the entry.
- `Observation:` for key observations, measurements, comparisons, or outcomes derived from outputs.
- `Limitation:` for limits, concerns, constraints, or reasons the result should not be overgeneralized.
- `Question:` for unresolved issues.
- `Decision:` for adopted, rejected, deferred, or operationalized choices.
- `Follow-up:` for local task-oriented work.
- `AI Use:` for AI assistance that materially affected what was retained, relied on, or decided.

Use callouts when they improve clarity. Do not force every important point into a callout.

Callouts are local to the section where they appear unless they clearly describe the whole entry.

They may include:

- purpose
- setup
- commands
- artifacts and links
- results
- interpretation
- decisions or retained conclusions
- open follow-up created by that evidence

Redundancy is acceptable in entries when it preserves context.

### Related

Use `Related:` with a reason, not just a bare ID. Use entry IDs within the same theme; use `theme-slug/entry-id` when the reference crosses themes:

```md
Related: `e004` supersedes this worker-count study.
Related: `benchmarking/e007` provides runtime evidence for this validation check.
```

For multiple related items:

```md
Related:
- `e004` supersedes this worker-count study.
- `benchmarking/e007` provides runtime evidence for this validation check.
```

### Code & Commands

Use `Code:` when the code state or code location matters to the evidence, result, or decision. Use `Command:` when the executable invocation matters. Most entries need one or the other, not both. Use both only when the code state and the exact command are separately important.

Examples:

```md
Code: Commit `abc1234`; changed `src/pipeline/cache.py` and `scripts/plot_runtime.py`.

Code: Analysis used notebook `analysis/runtime-check.ipynb` and supporting code in `src/model.py`.
```

Put executable commands in fenced code blocks. Prefer placing a command next to the image, statistics, table, or output it generated rather than collecting commands in a separate section by default. This preserves operational meaning for future agents. If the user later says to regenerate a plot or rerun a result, the needed command should be available next to the relevant output instead of requiring the agent to reconstruct the process.

### Output

Use `Output:` when the produced artifact is external to the prose and should be findable later, such as an image file, JSON file, CSV, NPZ file, model output, report, or generated asset path. If the output is inline in the entry, such as a figure, statistic, table, or quoted result, `Output:` is usually unnecessary.

### AI Use

Do not use `AI Use:` for trivial interaction, formatting help, or discarded suggestions. If a discarded suggestion creates a surviving question or task, record that under `Question:` or `Follow-up:`.

Prefer `AI Use:` as a one-line callout, with the label and text on the same line. Use a multi-line note only when the provenance is too complex to read clearly on one line.

Examples:

```md
AI Use: Agent wrote the extraction script for retained input artifacts and aggregation metadata. The researcher sanity-checked the script and output.

AI Use: Agent wrote the plotting script. The researcher sanity-checked the script and checked the plot against expectations.

AI Use: Agent compiled the run statistics and generated comparison plots without adding interpretation. The researcher validated this by inspecting the summary statistics, checking the plotted trends for consistency.

AI Use: Agent wrote the analysis code under researcher direction using the existing data-loading and plotting conventions. The researcher validated the retained output by inspecting the generated plots and summary statistics.

AI Use: Agent proposed the coordinate-frame mismatch as a possible explanation for the residual pattern. The researcher accepted it as a working interpretation and recorded follow-up checks to test whether the diagnostic plots and code path support it.

AI Use: Agent compared two implementation approaches and recommended the simpler cache layout. The researcher retained the decision after reviewing the tradeoff against current performance results and maintenance constraints.

AI Use: Agent implemented the refactor under researcher direction. The researcher validated it by reviewing the changed code paths and confirming that the new structure preserved the intended behavior.
```

## Maintenance Flow

Use this operational rule:

```text
Entry first, index always, summary only when understanding changes.
```

When research activity happens:

1. Identify the theme.
2. Check `<theme>/index.md` and use `## Concepts` to choose relevant concepts and a useful concept slug.
3. Create or update the entry under `<theme>/entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/index.md`.
4. Add entry-specific images, data, outputs, and scripts near the entry when needed.
5. Update `<theme>/index.md` so the entry ID resolves to the current path and the card gives enough routing context.
6. When new follow-up arises, record it in the dated entry first.
7. Update `<theme>.md` only if the new work changes current understanding, questions, decisions, follow-up view, or next steps.
8. Update supersession and related-entry references when the new work changes how older entries should be understood.

## Scripts

Use three layers for scripts.

- `<project>/scripts`: canonical reusable project scripts.
- `<theme>/scripts`: theme-specific reusable scripts.
- `<theme>/entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/scripts`: entry-specific tools used for one dated/topic entry.

This keeps reusable tools discoverable without forcing one-off research utilities into canonical project code.

When moving scripts into a research-log folder, identify any tests that appear to cover only those scripts. Ask whether those tests should stay, move with the script, or be removed. Keep tests that still cover public or canonical project behavior.

## Entry-Specific Artifacts

Images, generated outputs, and small entry-specific data files that belong to one dated/topic unit should live inside that entry.

Example:

```text
<theme>/entries/2026-04-18-runtime-e001-phase-14-worker-trade-study/
  index.md
  scripts/
  images/
  data/
  outputs/
```

Use these subfolders when useful:

- `images/`: plots, screenshots, figures, and rendered diagnostics used by the entry.
- `data/`: small or medium input or output data files needed to understand or reproduce the entry.
- `outputs/`: generated result artifacts that are not best understood as source data or images.
- `scripts/`: one-off tools specific to that entry.

Do not create empty supporting-material folders. Create `images/`, `data/`, `outputs/`, or `scripts/` only when adding files there or when `data/manifest.md` is needed.

The `index.md` file should provide the narrative, conclusions, artifact links, and enough context to explain why each artifact exists.

Do not copy large canonical datasets or project-level input stores into an entry by default. Link to their stable project path, external source, checksum, version, commit, or generation command instead. An entry folder should contain entry-specific evidence, not become a data warehouse.

### Data Manifest

Use `data/manifest.md` when an entry depends on external, large, canonical, or move-prone data assets. This is the preferred way to reference external data from entries.

The manifest gives each asset a short stable name that `index.md` can use instead of embedding fragile paths throughout the narrative.

Example:

```text
<theme>/entries/2026-04-18-runtime-e001-phase-14-worker-trade-study/
  index.md
  data/
    manifest.md
```

`index.md` can then refer to names such as `worker-summary-csv` or `full-run-parquet`, while `data/manifest.md` records the real locations and provenance.

Write entry commands from the perspective of the entry root as the working directory. This keeps logged commands local to the entry.

`skills/research-logging/scripts/pyrun` is the minimal Python command resolver for entry examples. It runs Python from the entry root, resolves manifest-backed data tokens, resolves theme-code and project-code tokens, and uses the project-local `.conda` Python when available.

When an entry first uses Python commands with manifest-backed inputs, project-level scripts, modules, or other project code, add an entry-root symlink named `pyrun` pointing to `skills/research-logging/scripts/pyrun`. Commands should then use short tokenized references:

```bash
./pyrun "<project>/scripts/plot_build_memory_timeline.py" "<dynamic-build-log>"
```

`pyrun` resolves `<project>` to the project root, `<project>/...` to a path under the project root, `<theme>` to the theme folder containing `index.md` and `entries/`, `<theme>/...` to a path under that theme folder, and `<Name>` to the matching `Location` in the nearest `data/manifest.md`. Tokens may appear as a whole argument or inside an argument, such as `static=<scheduler-series>/file.npz`. Quote arguments that contain angle tokens so the shell does not treat `<...>` as redirection. It searches `./manifest.md`, `./data/manifest.md`, then `../data/manifest.md`.

The manifest remains the source of truth for asset identity and location. The entry-root `pyrun` symlink is only a command convenience.

Recommended manifest columns:

- `Name`: short stable identifier used by `index.md`.
- `Type`: file or asset type.
- `Location`: path, URL, object store URI, or other durable reference.
- `Version / Commit`: version, commit, checksum, date, or other identity marker.
- `Notes`: why the asset matters or how it was used.

Do not require a manifest for small files copied directly into `data/`. Use it when indirection improves stability, provenance, or agent retrieval.
