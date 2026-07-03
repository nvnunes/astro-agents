# Review Operation Instructions

Use this file when the user asks to check whether a research log follows the recommended structure or is internally consistent.

`Review` is report-first. Do not edit files unless the user explicitly asks for fixes or the requested task clearly includes applying the fixes.

## Review Checklist

Depending on the requested review scope, look for:

- log-level structure that does not match the recommended summary-plus-entries shape (`skills/research-logging/SKILL.md`)
- entry folder names, entry IDs, split documents, or descriptive slugs that do not follow the recommended conventions (`skills/research-logging/references/file-entry-naming.md`)
- entry document shape or support-file placement that does not match entry guidance (`skills/research-logging/references/file-entry.md`)
- labels outside the recommended entry label list, especially when they reflect
  unclear vocabulary or make agent parsing less consistent (`skills/research-logging/references/file-entry-labels.md`)
- prose that does not match the recommended research-log writing style (`skills/research-logging/references/research-log-writing.md`)
- summary-vs-entry inconsistencies, including missing or stale `## Entries` links, stale or unsupported summary claims, entry content that changes current understanding but is not reflected in the summary, entry `Follow-up:` label items missing from `## Follow-ups`, stale follow-up items that remain in `## Follow-ups`, or summary claims that would benefit from supporting entry links (`skills/research-logging/references/file-summary.md`)
- inconsistent decision, validation, uncertainty, or evidence context across entries and summary
- broken or stale links in the summary, entries, or between entries
- citation keys that do not resolve to entries in `refs.bib` or citations that do not use the recommended bracketed inline-code key format (`skills/research-logging/references/file-references.md`)
- entry `pyrun` symlinks that do not resolve, `<name>` tokens in `pyrun` commands that do not match entries in `data.csv`, or `data.csv` files with missing required columns, unclear names, or locations that do not resolve or are not durable (`skills/research-logging/references/file-entry-commands.md`, `skills/research-logging/references/file-data-index.md`)
- retained script outputs that bypass `pyrun`, create unnecessary CSV files only to transfer table text into the entry, or link visual evidence that should be embedded inline (`skills/research-logging/references/file-entry-commands.md`)
- possible missing `AI Use:` notes where the log itself indicates agent work affected retained evidence, results, validation, uncertainty, or decisions (`skills/research-logging/references/file-entry-ai-use.md`)

## Output

Lead with findings ordered by importance. Include file paths and line references when possible. If no issues are found, say that and name any residual risk or unverified area.

If the user asks to apply fixes based on review findings, preserve source
wording unless the requested fix requires a specific rewrite. Do not omit,
condense, paraphrase, or materially rewrite source content without explicit
user direction.
