---
name: pubify-authoring
description: Work on pubify-pubs and pubify-ppt publication and presentation workflows, including pubs and ppt CLI behavior, pubify.yaml workspaces, configured publication or presentation roots such as papers or slides, figures.py, pub.yaml, ppt.yaml, generated figure/stat/table artifacts, PowerPoint anchors, and LaTeX publication outputs. Use when a task names pubify-pubs, pubify-ppt, pubs, ppt, or when ambiguous publication or presentation workflow work occurs in a subtree containing pub.yaml or ppt.yaml. Do not use for general scientific prose, generic Python work, or generic Matplotlib, LaTeX, or PowerPoint tasks outside a pubify workflow.
---

# Pubify Authoring

Pubify is a local-first workflow for turning Python-defined data, figures, stats, and tables into publication and presentation artifacts. `pubify-pubs` targets LaTeX publications; `pubify-ppt` targets editable PowerPoint decks. In both workflows, the host workspace owns the scientific content and local data, while pubify owns repeatable check, update, and export machinery, with build support for LaTeX publications.

Never use generative AI to create figures, stats, tables, or data as publication or presentation content. Figures, stats, and tables must be produced by executable code that works with real project data. The agent may help write, review, or debug that code, but the output must come from the code/data workflow. Do not invent data unless the user specifically asks for synthetic or draft data.

Fix sources, not generated outputs. When generated figures, stats, tables, TeX artifacts, generated PowerPoint artifacts, or build outputs are wrong, change the source data, `figures.py`, or helper code. Then rerun the appropriate pubify CLI. Do not hand-edit pubify-generated artifacts as the fix.

Do not invent scientific claims, hypotheses, facts, citations, or interpretation when writing publication or presentation text. Writing should be grounded in evidence from the user's workspace, cited references, generated values, or material the user explicitly provides. The agent may answer questions, outline, draft, revise, and tighten prose, but should mark uncertainty, ask for clarification, or look for supporting evidence when the basis for a statement is unclear.

## Reference Selection

Use the request and nearby files to select references:

- Read `references/pubify-pubs.md` when the task involves `pub.yaml`, a publication subtree, TeX source files, generated `autofigures`/`autostats`/`autotables`, or the `pubs` CLI.
- Read `references/pubify-ppt.md` when the task involves `ppt.yaml`, a presentation subtree, `.pptx` files, PowerPoint figure/table anchors or stat tokens, generated figure artifacts, or the `ppt` CLI.
- When the task is ambiguous and the current subtree contains `pub.yaml`, assume `pubify-pubs` unless other evidence points to presentations. When it contains `ppt.yaml`, assume `pubify-ppt` unless other evidence points to publications.
- Read `references/data-files.md` when working with pinned data, external data, or data from another pubify publication or presentation.
- Read `references/reuse.md` when reusing figures, stats, or tables from another publication or presentation.
- Read `references/figures-py.md` when changing `figures.py` data orchestration or dependency flow.
- Read `references/figure-export.md` when working on export-time mutation or shared Matplotlib export behavior.
- Read `references/pubify-pubs-figures.md` or `references/pubify-ppt-figures.md` when working on workflow-specific figure return values, panels, layouts, anchors, padding, or export options.

Pair with `$python-code-writing` for Python implementation and `$code-quality-review` for code review. For prose inside publication or presentation subtrees, default to `$science-writing` for manuscript text and slide text; follow any nearer `AGENTS.md` if it sets a different prose preference.
