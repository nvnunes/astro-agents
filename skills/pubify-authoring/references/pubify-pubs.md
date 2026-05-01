# Pubify Pubs

Use this reference for LaTeX publication workflows and `pubs` workflow work.

## Workspace Shape

A `pubify-pubs` workspace is rooted by `pubify.yaml` and has a configured publications root, commonly `papers`. Do not assume every subtree under that root is a pubify publication. A real `pubify-pubs` publication is a subtree with `pub.yaml`.

Inspect in this order:

1. top-level `pubify.yaml`
2. the publication's `pub.yaml`
3. `tex/main.tex`
4. `figures.py` when the task touches generated figures, stats, tables, source reuse, or pinned data
5. local TeX support files such as `tex/pubify.sty` or templates when the task is layout-related

When the target subtree has no `pub.yaml`, treat it as a plain TeX document or another project type that may consume pubify artifacts. Do not apply publication-workflow assumptions just because the path is under the configured publications root.

The publication workspace is the user-facing boundary. The host workspace owns scientific content, pinned inputs, manuscript text, helper code, and publication-specific configuration.

For manuscript prose and revisions, preserve scientific meaning, units, notation, citation keys, labels, cross-references, terminology, and LaTeX macro usage unless the user explicitly asks to change them. Prefer edits that are ready for direct inclusion in manuscript source and match the surrounding manuscript style.

Help the user with both coding and writing inside the publication workflow. Coding work may include ad hoc edits, loader or helper changes, figure/stat/table implementation, and validation. Writing work may include outlining, drafting, revising, tightening, and integrating manuscript text, captions, tables, references, and generated values.

Route publication work through existing project work outside the publication folder whenever possible. Do not invent data, results, analysis, citations, figures, claims, or implementation details unless the user explicitly asks for new draft material. If there is any hint that the needed work already exists, look for it first or ask the user for clarification.

## Source, Managed Files, And Output Rules

Treat `figures.py` as the publication's computational interface and `tex/main.tex` as the narrative interface.

### Editable Source

These files are normal edit targets when the user asks for manuscript, bibliography, local style, manual-figure, local data, config, or workflow changes:

- `pub.yaml`
- `figures.py`
- manuscript TeX, usually `tex/main.tex`
- split manuscript `.tex` files under `tex/`
- user-owned TeX support files, such as local `.sty` files that are not copied by pubify
- bibliography files such as `.bib`
- manual, non-generated figure files
- local data, config, and helper code

Help maintain bibliography files directly when the task involves references, citation keys, BibTeX metadata, or manuscript citation cleanup.

Manual figures and other non-pubify manuscript assets are ordinary source assets. They may live directly in `tex/` or below a `tex/` subfolder. Keep them where the manuscript owns them; the pubify workflow handles the build context.

### Pubify-Managed Support Files

These package-copied support files are managed inputs to the local publication:

- `tex/pubify.sty`
- `tex/pubify-template.tex`

Do not edit package-copied pubify support files directly. The `pubs` CLI refreshes them as part of the workflow.
Use the CLI workflow below instead of editing these files by hand.

### Generated Pubify Output

These files are generated output:

- `data/tex-artifacts/autofigures/`, exposed to TeX through `tex/autofigures`
- `data/tex-artifacts/autostats.tex`, exposed to TeX through `tex/autostats.tex`
- `data/tex-artifacts/autotables.tex`, exposed to TeX through `tex/autotables.tex`

Do not hand-edit generated pubify output as source. If a figure, stat, or table row is derived from pinned data, change `figures.py` or other external helpers as directed by the user, then regenerate.

### LaTeX Build Output

These files are build output:

- PDFs
- `.aux`, `.bbl`, `.blg`, `.fdb_latexmk`, `.fls`, `.log`, `.out`, `.synctex.gz`, and similar compiler files

Do not hand-edit LaTeX build output as source. If the PDF or compiler output is stale or wrong, fix the source/configuration and rerun the build process.

## Figures.py And Generated Artifacts

Read `data-files.md` for pinned data, external data, and data from another pubify publication or presentation. Read `reuse.md` when reusing generated figures, stats, or tables from another publication or presentation. Read `figures-py.md` for shared `figures.py` code organization. Read `pubify-pubs-figures.md` for publication figure layouts, `FigureResult`, panels, and export options. Read `figure-export.md` for export-time mutation or shared Matplotlib export behavior.

Generated artifacts live under each publication's `data/tex-artifacts/` tree and are exposed to TeX through pubify-managed symlinks such as `tex/autofigures`, `tex/autostats.tex`, and `tex/autotables.tex`. Do not create or edit those artifact symlinks by hand.

### Output IDs

Use publication-local output IDs derived from decorated function names:

```python
@figure
def plot_<figure_id>(ctx, ...):
    ...

@stat
def compute_<stat_id>(ctx, ...):
    ...

@table
def tabulate_<table_id>(ctx, ...):
    ...
```

The resulting IDs are used as follows:

- figure ID `<figure_id>` exports as `autofigures/<figure_id>.pdf` for a single-panel figure or `autofigures/<figure_id>_1.pdf`, `autofigures/<figure_id>_2.pdf`, and so on for multi-panel figures
- stat ID `<stat_id>` emits generated TeX macros in `autostats.tex`
- table ID `<table_id>` emits generated table macros in `autotables.tex`

Generated TeX macro names use PascalCase with a prefix:

- stat ID `training_summary` becomes `\StatTrainingSummary`
- keyed stat value `training_summary.mean_ee` becomes `\StatTrainingSummaryMeanEe`
- table ID `detection_summary` becomes `\TableDetectionSummary`
- multi-body table `detection_summary` is referenced as `\TableDetectionSummary{1}`, `\TableDetectionSummary{2}`, and so on

Keep Python IDs in snake_case. Use the generated PascalCase macro names in manuscript TeX.

Use stats aggressively for manuscript-facing computed values. If a sentence, caption, table note, or threshold depends on computed data, expose it with the stat decorator and reference the generated macro from TeX instead of typing the value directly into the manuscript.

For tables, return semantic rows from `figures.py`; do not build LaTeX table bodies in data logic. Put formatting decisions in the table result format metadata when the workflow supports it. If a table is too wide, prefer TeX-side layout changes before mutating the scientific data.

## Publication Configuration

`pub.yaml` owns publication-local workflow and export defaults. Use it for settings that apply to the publication as a whole. Use `figures.py`, `FigureResult`, or `panel(...)` for figure-specific behavior.

Common `pub.yaml` keys:

| Key | Use |
| --- | --- |
| `main_tex` | Publication-local TeX entrypoint, usually `main.tex` under `tex/`. |
| `mirror_root` | Optional mirror/sync target. Do not change unless the task is explicitly about mirror workflow. |
| `external_data_roots` | Named roots for `@external_data(...)` loaders; see `data-files.md`. |
| `sources` | Named source publications or presentations for output reuse; see `reuse.md`. |
| `sync_excludes` | Files excluded from mirror sync. |
| `pubify-mpl-template` | LaTeX template geometry used to size generated publication figures. |
| `pubify-mpl-defaults` | Publication-wide export defaults applied before per-figure or per-panel overrides. |

Example:

```yaml
main_tex: main.tex
external_data_roots:
  shared_results: /Volumes/Data/results/large-run
sources:
  related_publication: papers/related-publication
pubify-mpl-template:
  textwidth_in: 5.39643
  textheight_in: 7.58960
  base_fontsize_pt: 12
  axes_line_width_pt: 0.8
  tick_length_pt: 3.0
  caption_lineheight_pt: 13.6
  subcaption_lineheight_pt: 13.6
  row_skip_in: 0.11
  caption_skip_in: 0.11
  subcaption_skip_in: 0.08
  subcaption_allowance_in: 0.08
  caption_allowance_in: 0.08
pubify-mpl-defaults:
  layout: onewide
  extra_rcparams:
    mathtext.default: regular
```

Use `pubify-mpl-template` to keep generated figure geometry aligned with the manuscript's LaTeX template. To get the core values from the manuscript, temporarily add `\figprintlayoutspec` to the TeX source, build the document, and copy the printed `FIG_DOCUMENT_INFO` values into `pub.yaml`. Keep the remaining template spacing values unless the publication's figure spacing rules also need to change.

Use `pubify-mpl-defaults` for defaults that should apply to every generated publication figure unless a figure overrides them. See `pubify-pubs-figures.md` for publication figure layouts and export options.

## CLI Workflow

The installed command is `pubs`. Use it from the workspace that contains `pubify.yaml`. Prefer the CLI for pubify operations instead of manually recreating what the workflow already manages.

If `pubs` is not available, use the target project's existing Python environment and run its equivalent of `pip install pubify-pubs`. After installation, verify the command with `pubs --help`.

| Command | Use |
| --- | --- |
| `pubs list` | List configured publications. |
| `pubs init` | Initialize workspace-level pubify-pubs configuration. |
| `pubs init <publication-id>` | Create a publication scaffold. |
| `pubs <publication-id> shell` | Open the publication shell for repeated commands. |
| `pubs <publication-id> data list` | List data loaders. |
| `pubs <publication-id> data add <data-id>` | Add a data-loader stub to `figures.py`. |
| `pubs <publication-id> figure list` | List generated figure IDs. |
| `pubs <publication-id> figure add <figure-id>` | Add a figure stub to `figures.py`. |
| `pubs <publication-id> figure update` | Regenerate all figures. |
| `pubs <publication-id> figure <figure-id> update` | Regenerate one figure. |
| `pubs <publication-id> figure <figure-id> preview [<subfig-idx>]` | Preview one generated figure or panel. |
| `pubs <publication-id> figure <figure-id> latex [subcaption]` | Generate a figure LaTeX scaffold, optionally with subcaption slots, to copy into manuscript TeX. |
| `pubs <publication-id> stat list` | List generated stat IDs. |
| `pubs <publication-id> stat add <stat-id>` | Add a stat stub to `figures.py`. |
| `pubs <publication-id> stat update` | Regenerate all stats. |
| `pubs <publication-id> stat <stat-id> update` | Recompute one stat and rewrite the stats snapshot. |
| `pubs <publication-id> stat <stat-id> latex` | Generate a stat macro scaffold to copy into manuscript TeX. |
| `pubs <publication-id> table list` | List generated table IDs. |
| `pubs <publication-id> table add <table-id>` | Add a table stub to `figures.py`. |
| `pubs <publication-id> table update` | Regenerate all tables. |
| `pubs <publication-id> table <table-id> update` | Recompute one table and rewrite the tables snapshot. |
| `pubs <publication-id> table <table-id> latex` | Generate a table macro scaffold to copy into manuscript TeX. |
| `pubs <publication-id> update` | Refresh package-copied support files and regenerate figures, stats, and tables. |
| `pubs <publication-id> build [--clear]` | Refresh package-copied support files, validate, and compile the current TeX tree; does not regenerate figures, stats, or tables. |
| `pubs <publication-id> preview` | Preview the compiled publication. |

Use `pubs <publication-id> update` when generated figures, stats, or tables may be stale. `update` performs the full pubify-generated artifact refresh.

Use `pubs <publication-id> build` to refresh package-copied support files, validate, and compile the current publication-local TeX tree. `build` will pick up changed TeX and manuscript assets through the LaTeX build process, but it does not regenerate figures, stats, or tables, so run `update` first when generated outputs may have changed.

Use targeted figure, stat, and table updates while iterating on one output. Use `preview` for the compiled publication or for an exported figure. Use the `latex` commands as read-only helpers to print snippets for manuscript integration; they do not edit manuscript files.

Debug update and build failures separately. Update failures are usually loader, pinned-data, plotting, or artifact-generation issues. Build failures are often TeX-side issues.

## Before Finishing

- If generated figures, stats, or tables may have changed, use the appropriate `pubs` update command before build validation.
- If manuscript TeX changed, use `pubs <publication-id> build` for validation when practical.
- If the task touched generated output, confirm the source change was made in `figures.py` or an external helper, not in generated artifacts.
- If bibliography or citation keys changed, check that manuscript citations and `.bib` entries stay aligned.
