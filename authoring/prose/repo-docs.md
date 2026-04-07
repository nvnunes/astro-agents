# Repo Docs

## Purpose
Use this style for repository-facing documentation such as `README.md`, `docs/architecture.md`, `docs/testing.md`, `docs/api.md`, `docs/glossary.md`, `CONTRIBUTING.md`, and similar long-lived operational, reference, or design documents. Inherit the common documentation-writing discipline from `authoring/prose/base.md`, then apply the additional rules below.

## Success Criteria
- Make the document easy to scan and use as an orientation, source-of-truth, or operational reference.
- Improve clarity, precision, and structure without blurring document boundaries.
- Keep commands, paths, interfaces, boundaries, and source-of-truth references explicit.

## Repo-Docs-Specific Requirements
- Make the document's role obvious near the top.
- Lead with orientation, scope, or what the document is authoritative for.
- Keep setup, commands, and where-to-go-next guidance close to the sections that depend on them.
- Keep commands, paths, interfaces, boundaries, and source-of-truth references explicit.
- Keep source-of-truth boundaries explicit between `README.md`, `AGENTS.md`, architecture docs, testing docs, and plans.
- Link to deeper documents instead of duplicating them.
- Do not let one repo doc drift into doing the job of another.

## Preservation And Revision
When revising existing repo docs:
- Preserve source-of-truth boundaries between `README.md`, `AGENTS.md`, architecture docs, testing docs, and plans.
- Do not let repo-document motivation or narrative displace operational clarity.
- Do not turn a narrow operational doc into a general overview.
- Do not duplicate instructions that should be linked instead.
- Do not change commands, paths, API names, configuration keys, or workflow steps unless instructed or clearly required to fix an error.
- Keep cross-references accurate.
- Improve scanability, precision, and section structure.

## Adaptation
Adjust emphasis by document type without changing the overall tone:
- `README.md`: prioritize orientation, entrypoints, setup, and where to go next.
- `docs/architecture.md`: prioritize boundaries, ownership, interfaces, and data/control flow.
- `docs/testing.md`: prioritize canonical commands, verification expectations, and test-layer boundaries.
- `docs/glossary.md`: prioritize stable definitions, term boundaries, and consistent cross-references to the documents that use those terms.
- `docs/api.md`: prioritize stable interfaces, inputs/outputs, and usage constraints.
- `CONTRIBUTING.md`: prioritize workflow, review expectations, and contribution steps.
- If document type is unclear, infer it from the surrounding material. If it remains unclear, default to `README.md`-style orientation.

## Output
- When writing or revising a repo doc, return the revised document directly unless explanation is requested.
- When reviewing a repo doc, identify role drift, weak source-of-truth boundaries, weak scanability, or weak cross-linking before proposing replacement text.
