# README.md

## Purpose
Use this style for `README.md` files. Inherit the common documentation-writing discipline from `authoring/writing/base.md` and the shared project-documentation discipline from `authoring/writing/project-docs.md`, then apply the `README.md`-specific rules below.

## Role
- Treat `README.md` as a special project document: the main human-facing starting document into the project or folder it describes.
- Make it useful to a reader arriving with little or no prior context.
- Make it clear what the project or folder is, why it exists, and where the reader should go next.

## Requirements
- Make the document's role obvious near the top.
- State identity, scope, and value near the top.
- Lead with what the project or folder is and what it contains.
- Keep the opening human-facing; do not lead with documentation mechanics unless that distinction is essential.
- Include clear setup, starting documents, or where-to-go-next guidance when deeper docs matter.
- Make important source-of-truth docs discoverable from the README.
- Keep routing-and-workflow rules, instruction authority, and deeper operational detail in `AGENTS.md`, architecture docs, testing docs, validation docs, or plans rather than explaining them fully here.
- Use structure and headings that help a new reader scan quickly.
- Keep the README focused on orientation and discoverability rather than full operational detail.

## Top-Level Vs Folder README.md
- A top-level project `README.md` has the strongest starting document obligation: identify the project, explain its value, and point readers to the main deeper docs.
- A folder-level `README.md` may be narrower, but it should still explain the folder's purpose, what lives there, and any important next documents or related prompts.
- Do not make a folder-level `README.md` try to replace the project root `README.md` or broader source-of-truth docs.

## Preservation And Revision
When revising an existing `README.md`:
- Preserve its starting document role.
- Remove or relocate material that really belongs in architecture, testing, validation, or plan docs.
- Keep cross-references accurate and current.
- Prefer short orientation and direct links over long repeated explanation.

## Output
- When writing or revising a `README.md`, return the revised document directly unless explanation is requested.
- When reviewing a `README.md`, prioritize weak orientation, missing starting documents, role drift, weak discoverability of deeper docs, or unnecessary duplication of other source-of-truth material.
