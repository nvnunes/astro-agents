# Source Document Upgrade Operation Instructions

Use this file when upgrading an existing source document into a theme-document hierarchy.

This operation is a human-agent loop, not an automatic rewrite.

## Approval Guard

Do not create, edit, move, rename, or delete files until the human has explicitly approved the proposed upgrade plan.

Approval to propose a plan is not approval to perform the split. After plan approval, preserve source wording and do not omit, condense, paraphrase, or materially rewrite source content unless the human explicitly approves that specific transformation.

During an upgrade, do not rewrite entry shards to satisfy `skills/research-logging/references/research-log-entry-writing.md`. Use that guide only for entry openings, labels, headings, retrieval clarity, and any new connective prose the agent adds. Source content copied into entries remains preserved unless the human explicitly approves rewriting.

Draft `<theme>.md` through `skills/research-logging/references/file-summary.md`. The initial summary should be direct, compressed, scannable, and written as a living current-state document rather than copied source text.

## Procedure

1. Review the source document before changing files.
2. If either more entries with fewer sections or fewer entries with more sections would be reasonable, ask the human which decomposition style to prefer.
3. Propose a plan after any needed decomposition preference is resolved.
4. Include proposed concepts and sub-concepts, preferring less depth unless the human asks for more.
5. Include entries to create.
6. Include proposed entry paths or filename components for each entry: date, concept slug, `e###` entry ID, and descriptive topic slug. Entry IDs are independent of concept, theme, version, and status labels.
7. Include files to move into entries.
8. Include scripts to move into theme or entry folders.
9. Include tests that appear to cover only scripts being moved, with a proposed stay, move, or remove action.
10. Include entries that need `data/manifest.md` for external, large, canonical, or move-prone data assets.
11. Write entry commands from the perspective of the entry root as the working directory.
12. If entry Python commands will use manifest-backed assets, theme-shared scripts, project-level scripts, modules, or other code, include an entry-root `pyrun` symlink for those entries and write commands with `./pyrun`, `<Name>` data tokens, `<theme>/...` theme-code tokens, and `<project>/...` project-code tokens rather than raw external paths, relative `../../scripts` paths, hard-coded project paths, environment activation lines, or `project=$PWD` setup.
13. Include whether project-local `Research Logs` recognition in `AGENTS.md` should be updated for the new theme. If the downstream project has a `## Research Logs` section or another clear research-log theme registry, include the registry update in the plan. If no registry exists, ask before adding one. Add or revise registry entries as compact bullets that map the theme name inline to the `<theme>.md` file and matching `<theme>/` folder and likely aliases.
14. Include proposed `AI Use:` labels raised by the source document, or recommend optional human-guided retrospective `AI Use:` review when material AI use cannot be inferred.
15. Include summary content already present in the source document.
16. Wait for the human to adjust and approve the plan.
17. Perform the split after approval.
18. Draft `<theme>.md` as an initial living summary.
19. Wait for the human to adjust and approve the summary.
20. If the approved plan includes optional retrospective `AI Use:` review, perform it as a human-guided entry-by-entry review after the upgrade is complete.

## Split Rules

- Treat the split primarily as `Capture`.
- Entry shards must preserve original content.
- Labels and light reorganization may be added when they improve retrieval or clarity.
- Do not silently rewrite, paraphrase, omit, condense, or materially change source evidence.
- If content does not clearly belong in an entry or summary, preserve it in an entry or flag it for human decision.
- Add `AI Use:` labels only when approved by the human during the upgrade plan or later explicit direction.
- If AI involvement is plausible but unapproved or uncertain, preserve only specific, actionable questions in the affected entry as `Question:` or `Follow-up:`.
- When moving scripts into a research-log folder, identify any tests that appear to cover only those scripts. Ask whether those tests should stay, move with the script, or be removed. Keep tests that still cover public or canonical project behavior.
- Update project-local `Research Logs` recognition in `AGENTS.md` only when that update was included in the approved upgrade plan. If no registry exists and the human did not approve adding one, leave `AGENTS.md` unchanged and report that the theme is reachable by explicit path only.
- Draft the initial `<theme>.md` through the summary guide. Preserve source meaning and links to evidence, but do not preserve source wording by default in the summary.

Classify summary-like material as one of:

- evidence-local interpretation that belongs in an entry
- current understanding that should be preserved in `<theme>.md`
- ambiguous material that needs human direction

## Retrospective AI-Use Review

Retrospective `AI Use:` review is not an autonomous audit.

For each entry under review, present the entry ID, path, and short description; ask whether material AI assistance affected anything retained, relied on, or decided; draft an `AI Use:` note only from human confirmation or clearly surviving source provenance; and add the note only after the human approves the wording.

When drafting or revising retrospective `AI Use:` notes, read `skills/research-logging/references/file-entry-ai-use.md` for wording rules and examples.

If AI involvement remains plausible but unconfirmed, do not speculate. Add `Question:` or `Follow-up:` only when the human identifies a concrete provenance check worth preserving.

## Files To Consult

- For capture behavior and supporting materials, read `skills/research-logging/references/operation-capture.md`.
- For entry, index, summary, or manifest structure, read the matching `file-*.md` guide only when needed.

## Completion

After the split and draft summary, report entries created, files moved or intentionally left in place, script-only test decisions approved by the human, AI-use notes added or review offered, project-local `Research Logs` recognition updated or intentionally left unchanged, summary content preserved in `<theme>.md`, and items left for human decision.
