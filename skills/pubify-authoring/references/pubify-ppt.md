# Pubify PPT

Use this reference for PowerPoint presentation workflows and `ppt` workflow work.

## Workspace Shape

A `pubify-ppt` workspace is rooted by `pubify.yaml` and has a configured presentations root, commonly `slides`. Do not assume every subtree under that root is a pubify presentation. A real `pubify-ppt` presentation is a subtree with `ppt.yaml`.

Inspect in this order:

1. top-level `pubify.yaml`
2. the presentation's `ppt.yaml`
3. the editable `.pptx` source deck when layout, anchors, or generated placement matter
4. `figures.py` when the task touches generated figures, stats, tables, source reuse, or pinned data

When the target subtree has no `ppt.yaml`, treat it as a plain PowerPoint deck or another project type that may consume pubify artifacts. Do not apply presentation-workflow assumptions just because the path is under the configured presentations root.

The presentation workspace is the user-facing boundary. The host workspace owns scientific content, pinned inputs, slide text, helper code, the editable source deck, and presentation-specific configuration.

For slide text and speaker-facing scientific phrasing, preserve scientific meaning, units, notation, citation keys, labels, cross-references, and terminology unless the user explicitly asks to change them. Prefer concise slide text that is ready for direct inclusion in the deck and matches the surrounding slide style.

Help the user with both coding and writing inside the presentation workflow. Coding work may include ad hoc edits, loader or helper changes, figure/stat/table implementation, anchor repair, and validation. Writing work may include outlining, drafting, revising, tightening, and integrating slide text, figure captions, table content, references, and generated values.

Route presentation work through existing project work outside the presentation folder whenever possible. Do not invent data, results, analysis, citations, figures, claims, or implementation details unless the user explicitly asks for new draft material. If there is any hint that the needed work already exists, look for it first or ask the user for clarification.

## Source, Managed Files, And Output Rules

Treat `figures.py` as the presentation's computational interface and the editable `.pptx` deck as the visual and narrative interface.

### Editable Source

These files are normal edit targets when the user asks for deck, slide text, local data, config, or workflow changes:

- `ppt.yaml`
- `figures.py`
- the editable source deck, usually `deck.pptx`
- local data, config, and helper code
- manual, non-generated presentation assets

Layout changes normally belong in the editable `.pptx` deck. Keep generated figure placeholders, stat text boxes, and native tables compatible with the managed anchor model.

Do not use Python PowerPoint tooling such as `python-ppt` to edit the deck directly unless the user explicitly asks for that. It is acceptable to inspect the deck with Python tooling when diagnosing layout, anchor, token, or corruption issues. Use the `ppt` CLI workflow to make deck changes or direct the user to do so through PowerPoint.

### Generated Pubify Output

These files are generated output:

- generated figure PNGs under `data/ppt-artifacts/figures/`

Do not hand-edit generated pubify output as source. If a figure, stat, or table row is derived from pinned data, change `figures.py` or other external helpers as directed by the user, then regenerate.

Deck backups live under `data/ppt-artifacts/backups/`. The editable source deck remains the source of truth, and `ppt` update commands change it directly. If the deck is corrupted by an update, recover from the most recent backup.

## Figures.py And Generated Artifacts

Read `data-files.md` for pinned data, external data, and data from another pubify publication or presentation. Read `reuse.md` when reusing generated figures, stats, or tables from another publication or presentation. Read `figures-py.md` for shared `figures.py` code organization. Read `pubify-ppt-figures.md` for PowerPoint figure return values, panels, anchors, and presentation figure export options. Read `figure-export.md` for export-time mutation or shared Matplotlib export behavior.

Generated artifacts live under each presentation's `data/ppt-artifacts/` tree. Generated PNGs are embedded into the PowerPoint file during update, so `data/ppt-artifacts/` is an intermediate workflow area rather than the final presentation surface. Do not edit generated PNGs or backups by hand.

### Output IDs

Use output IDs derived from decorated function names:

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

- figure ID `<figure_id>` exports as `data/ppt-artifacts/figures/<figure_id>.png` for a single-panel figure or `<figure_id>_1.png`, `<figure_id>_2.png`, and so on for multi-panel figures, then updates matching PowerPoint figure anchors
- stat ID `<stat_id>` replaces matching PowerPoint stat tokens
- table ID `<table_id>` updates matching PowerPoint table anchors

PowerPoint anchors and tokens use these output IDs:

- figure anchors use `{{fig:<figure_id>}}`
- multi-panel figure anchors use `{{fig:<figure_id>:1}}`, `{{fig:<figure_id>:2}}`, and so on
- stat tokens use `{{stat:<stat_id>}}` or `{{stat:<stat_id>.<key>}}`
- table anchors use `{{table:<table_id>}}`

Pubify uses shape Alt Text to find and update managed PowerPoint outputs. Use `addto` CLI commands to add figure anchors, stat tokens, and table anchors to existing slides in the editable deck. Slide numbers are one-based. Do not remove managed Alt Text anchors unless the goal is for pubify to stop updating that output.

Use stats aggressively for slide-facing computed values. If slide text, a caption, table text, or a threshold depends on computed data, expose it with the stat decorator and reference the generated token from PowerPoint instead of typing the value directly into the deck.

Stats use token replacement. Keep stat tokens intact in the editable deck and rerun the `ppt` workflow when the computed value changes. Use one stat token per text box.

Tables use native PowerPoint tables. Table anchors use a placeholder's visible text or Alt Text. Update replaces the placeholder with a native PowerPoint table and persists the token in Alt Text. Later updates preserve the heading row and require the table to keep the same total row count and column count. Multi-body table results are not supported.

Keep managed anchors and tokens in ordinary, ungrouped PowerPoint shapes. Avoid placing them inside grouped, rotated, cropped, animated, chart, SmartArt, embedded-object, or table-contained shapes. Use `ppt <presentation-id> check` when anchor support is uncertain.

## Presentation Configuration

`ppt.yaml` owns presentation-local workflow defaults. Use it for settings that apply to the presentation as a whole. Use `figures.py`, `FigureResult`, or `panel(...)` for figure-specific behavior.

Common `ppt.yaml` keys:

| Key | Use |
| --- | --- |
| `deck` | Presentation-local editable source deck path, usually `deck.pptx`. |
| `backup_retention` | Number of in-place deck backups to retain under `data/ppt-artifacts/backups/`. |
| `defaults` | Presentation-wide generated-figure defaults. |
| `external_data_roots` | Named roots for `@external_data(...)` loaders; see `data-files.md`. |
| `sources` | Named source publications or presentations for output reuse; see `reuse.md`. |

Example:

```yaml
deck: deck.pptx
backup_retention: 5
defaults:
  dpi: 200
  figure_font_family: Aptos
  figure_base_fontsize_pt: 11
  figure_axes_labelsize_pt: 11
  figure_tick_labelsize_pt: 10
  figure_legend_fontsize_pt: 10
  figure_title_fontsize_pt: 12
external_data_roots:
  shared_results: /Volumes/Data/results/large-run
sources:
  related_publication: papers/related-publication
```

Use `defaults` when generated figures should match the deck consistently.

`defaults.figure_font_family` overrides the deck theme font for generated figures. If it is not set, pubify tries to use the deck theme body font when Matplotlib can resolve it. The figure-size defaults become the baseline style for generated figures, and `FigureResult(..., metadata={"style": {...}})` or `panel(..., style={...})` can override them for one figure or panel.

## CLI Workflow

The installed command is `ppt`. Use it from the workspace that contains `pubify.yaml`. Prefer the CLI for pubify operations instead of manually recreating what the workflow already manages.

If `ppt` is not available, use the target project's existing Python environment and run its equivalent of `pip install pubify-ppt`. After installation, verify the command with `ppt --help`.

| Command | Use |
| --- | --- |
| `ppt list` | List configured presentations. |
| `ppt init` | Initialize workspace-level pubify-ppt configuration. |
| `ppt init <presentation-id>` | Create a presentation scaffold. |
| `ppt <presentation-id> check` | Validate config, dependencies, anchors, tokens, and supported shapes. |
| `ppt <presentation-id> data list` | List data loaders. |
| `ppt <presentation-id> figure list` | List generated figure IDs. |
| `ppt <presentation-id> figure <figure-id> addto <slide-number>` | Add a centered figure anchor to an existing slide and immediately run the targeted figure update. |
| `ppt <presentation-id> figure <figure-id>:<panel-number> addto <slide-number>` | Add a centered multi-panel figure anchor to an existing slide and immediately run the targeted figure update. |
| `ppt <presentation-id> figure update` | Regenerate all figures and update figure anchors. |
| `ppt <presentation-id> figure <figure-id> update` | Regenerate one figure and update matching figure anchors. |
| `ppt <presentation-id> stat list` | List generated stat IDs. |
| `ppt <presentation-id> stat <stat-id> addto <slide-number>` | Add a centered stat token text box to an existing slide and immediately run the targeted stat update. |
| `ppt <presentation-id> stat update` | Recompute all stats and update stat tokens. |
| `ppt <presentation-id> stat <stat-id> update` | Recompute one stat and update matching stat tokens. |
| `ppt <presentation-id> table list` | List generated table IDs. |
| `ppt <presentation-id> table <table-id> addto <slide-number>` | Add a centered table anchor to an existing slide and immediately run the targeted table update. |
| `ppt <presentation-id> table update` | Recompute all tables and update table anchors. |
| `ppt <presentation-id> table <table-id> update` | Recompute one table and update matching table anchors. |
| `ppt <presentation-id> update` | Refresh figures, stats, and tables, then write the deck once. |

Use `ppt <presentation-id> check` before changing anchors, config, dependencies, unsupported shapes, or ambiguous stat/table behavior.

Use `addto` commands to place new managed outputs. If a multi-panel figure omits the panel number, `addto` creates a panel-1 anchor before rendering. Use targeted figure, stat, and table updates while iterating on one output. Use `ppt <presentation-id> update` when multiple generated outputs may be stale or when the full deck should be refreshed.

Do not open PowerPoint from automation, and do not run `ppt` commands while the deck is open in PowerPoint. If PowerPoint has the deck open, lock-file detection should fail early; otherwise in-place writes can be risky on macOS.

## Before Finishing

- If generated figures, stats, or tables may have changed, use the appropriate `ppt` update command.
- If anchors, config, dependencies, or supported shape assumptions changed, use `ppt <presentation-id> check` when practical.
- If the task touched generated output, confirm the source change was made in `figures.py` or an external helper, not in generated PNGs or backups.
