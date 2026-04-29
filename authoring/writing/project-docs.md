# Project Documentation

## Purpose
Use this style for project-facing documentation such as `README.md`,
`docs/architecture.md`, `docs/testing.md`, `docs/api.md`,
`docs/glossary.md`, `CONTRIBUTING.md`, and similar long-lived operational,
reference, or design documents. Use it as well for generated
reference-doc inputs such as docstrings or docs-generation config when they
define published project documentation. Inherit the common documentation-writing
discipline from `authoring/writing/base.md`, then apply the additional rules
below.

## Success Criteria
- Make the document easy to scan and use as an orientation, source-of-truth, or operational reference.
- Improve clarity, precision, and structure without blurring document boundaries.
- Keep commands, paths, interfaces, boundaries, and source-of-truth references explicit.

## Project Documentation Requirements
- Make the document's role obvious near the top.
- Lead with orientation, scope, or what the document is authoritative for.
- Keep setup, commands, and where-to-go-next guidance close to the sections that depend on them.
- Keep commands, paths, interfaces, boundaries, and source-of-truth references explicit.
- Keep source-of-truth boundaries explicit between `README.md`, `AGENTS.md`, architecture docs, testing docs, and plans.
- Treat generated reference-doc inputs as part of the same source-of-truth system when they define published docs.
- Link to deeper documents instead of duplicating them.
- Do not let one project document drift into doing the job of another.

## Preservation And Revision
When revising existing project documentation:
- Preserve source-of-truth boundaries between `README.md`, `AGENTS.md`, architecture docs, testing docs, and plans.
- Do not let project-documentation framing or narrative displace operational clarity.
- Do not turn a narrow operational doc into a general overview.
- Do not duplicate instructions that should be linked instead.
- Do not change commands, paths, API names, configuration keys, or workflow steps unless instructed or clearly required to fix an error.
- Keep cross-references accurate.
- Improve scanability, precision, and section structure.

## Adaptation
Adjust emphasis by document type without changing the overall tone:
- `README.md`: prioritize orientation, starting documents, setup, and where to go next.
- `docs/architecture.md`: prioritize boundaries, ownership, interfaces, and data/routing and workflow.
- `docs/testing.md`: prioritize canonical commands, verification expectations, and testing-scope boundaries.
- `docs/glossary.md`: prioritize stable definitions, term boundaries, and consistent cross-references to the documents that use those terms.
- `docs/api.md`: prioritize stable interfaces, inputs/outputs, and usage constraints.
- `CONTRIBUTING.md`: prioritize workflow, review expectations, and contribution steps.
- If document type is unclear, infer it from the surrounding material. If it remains unclear, default to `README.md`-style orientation.

## Output
- When writing or revising project documentation, return the revised document directly unless explanation is requested.
- When reviewing project documentation, identify role drift, weak source-of-truth boundaries, weak scanability, or weak cross-linking before proposing replacement text.
