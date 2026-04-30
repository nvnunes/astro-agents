# Research Log

This directory contains references for reusable research-log concepts and supporting infrastructure in `astro-agents`.

This area is first-class in the library, but its schema is still evolving. Material here supports agent-first research documentation, source-plus-summary organization, and skill or template design that can help downstream projects maintain research logs. Do not treat this directory as a finalized research-log schema.

## Role In Astro-Agents

Use this area as supporting context after `skills/research-logging/SKILL.md` selects a research-log operation involving theme-document hierarchies, source-plus-summary research logs, routine capture, or source-document upgrades.

The user-facing entrypoint is `skills/research-logging/SKILL.md`. These references are bundled skill material, not downstream adoption examples.

## Current Contents

- `skills/research-logging/references/theme-document-pattern.md`: reusable theme-document pattern for project-native research-log hierarchies.
- `skills/research-logging/references/theme-routing.md`: compact router for routine theme-document maintenance instructions.
- `skills/research-logging/references/operation-*.md`: setup, source-document conversion, capture, summary, check, and concept operations.
- `skills/research-logging/references/file-*.md`: shared file-writing guides.
- `skills/research-logging/scripts/pyrun`: minimal Python command resolver for entry examples.

## Working Model

Research-log conventions should remain flexible because researchers and projects organize work differently. The research log should not require one fixed top-level location.

A promising pattern is a theme document plus a matching folder. The theme document is a first-class living summary. The theme folder carries the timeline index, dated evidence entries, and entry-specific artifacts.

The theme-document pattern's contribution is not the folder location itself. Its contribution is how the hierarchy is implemented so agents can retrieve context efficiently, distinguish the living summary from historical evidence, preserve provenance, and update summaries without loading everything.

See `skills/research-logging/references/theme-document-pattern.md` for the current reusable pattern and `skills/research-logging/references/theme-routing.md` for routine maintenance routing.

## Starter Requests

To create a new research-log theme, use a prompt such as:

- `Create a research-log theme for <theme>.`
- `Start a new research-log for <theme>.`
- `Set up <theme> as a research-log.`

To upgrade an existing source document into a research-log theme, use:

- `Upgrade <path-to-source-document> into a research-log theme using astro-agents.`

To continue an existing theme when the downstream project has local `Research Logs` recognition in `AGENTS.md`, use natural prompts such as:

- `Let's work on <theme>.`
- `Capture this in <theme>.`
- `Update the <theme> summary.`

`<theme>` should be a path to `<theme>.md`, `<theme>/`, a named theme from project-local `Research Logs` recognition, or a theme that is contextually obvious from the chat.

After the theme is established, use ordinary research wording such as:

- Concepts:
  - `Add a concept for <concept>.`
  - `Start a new concept for <concept>.`
  - `Reorganize the concepts.`
  - `Associate this entry with <concept>.`
- New entries:
  - `Capture this in a new entry.`
  - `Create a new entry for <topic>.`
  - `Add this as a new entry.`
- Existing entries:
  - `Continue the current entry.`
  - `Work on <entry>.`
  - `Add this to the entry from <date>.`
- Summary:
  - `Update the summary.`
  - `Update the summary from the current entry.`
  - `Reflect this entry in the summary.`
- Check:
  - `Review the summary against the log.`
  - `Check the summary links.`
  - `Audit whether the summary matches the entries.`

`<entry>` can be an entry ID, path, topic, date, or inferred from chat context.

Plain-language synonyms should work. For example, `entry` may be phrased as `file` or `record`, and `add` may be phrased as `create` or `capture`.

Requests about summary format, organization, or emphasis should update `<theme>.md` directly.

## Working Principles

- Keep canonical source material in durable files.
- Treat `<theme>.md` as a first-class living summary that points back to dated source evidence.
- Let researchers place research-log hierarchies where they fit the project.
- Optimize for agent retrieval and human review.
- Preserve provenance without prematurely formalizing disclosure or ownership review.
- Let skill references and templates emerge from documented practice rather than creating them first.

## Deferred

- No final research-log schema is defined here yet.
