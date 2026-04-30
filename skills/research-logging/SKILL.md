---
name: research-logging
description: Create, convert, capture, update, check, or maintain research-log theme hierarchies, source-plus-summary logs, dated entries, living summaries, indexes, concepts, AI-use notes, data manifests, scripts, and artifacts. Use for research-log lifecycle work, not general project docs or scientific manuscript writing.
---

# Research Logging

Use this skill for the full lifecycle of project-native research logs: starting a theme, converting source documents, capturing new work, maintaining summaries, checking consistency, managing concepts, and preserving supporting artifacts.

Read `references/theme-routing.md` first for operation selection and shared safety rules. Infer the operation from ordinary user language rather than requiring the user to name it.

Choose the operation:

- New empty theme or initial skeleton: read `references/operation-create-theme.md`.
- Existing source document conversion: read `references/operation-upgrade-source-document.md`.
- New evidence, work, artifacts, scripts, data, or decisions to preserve: read `references/operation-capture.md`.
- Targeted living-summary update: read `references/operation-summary-update.md`.
- Summary, links, concepts, index, or consistency audit: read `references/operation-summary-check.md`.
- Concept creation, revision, reorganization, or association: read `references/operation-manage-concepts.md`.

For source-document upgrades, do not create, edit, move, rename, or delete files until the human explicitly approves the proposed upgrade plan. Preserve source wording unless the human approves a specific transformation.

Use detailed references only when needed:

- Research-log hierarchy concept and full design: `references/theme-document-pattern.md`.
- Summary file structure: `references/file-summary.md`.
- Index and concept structure: `references/file-index.md`.
- Dated entry structure: `references/file-entry.md`.
- AI-use note wording: `references/file-entry-ai-use.md`.
- Entry-local data manifest structure: `references/file-data-manifest.md`.
- Entry prose style: `references/research-log-entry-writing.md`.

Use `scripts/pyrun` for manifest-backed entry-local Python command examples when an entry depends on data tokens, theme-code tokens, or project-code tokens.
