# Research Log

This directory is the shared prompt family for reusable research-log concepts and supporting infrastructure in `astro-agents`.

This area is first-class in the library, but its schema is still evolving. Material here supports agent-first research documentation, source-plus-summary organization, and prompt or template design that can help downstream repos maintain research logs. Do not treat this directory as a finalized research-log schema.

## Role In Astro-Agents

Use this area when a task concerns reusable research-log guidance, theme-document hierarchies, source-plus-summary research logs, or upgrading an existing source document into a theme record.

This area is part of the routed shared prompt surface. It is not a downstream recommendation-doc family like `guidance/`, and it is not a repo-local validation family like `agents/`.

## Current Contents

- `research-log/themes/README.md`: reusable theme-document pattern for project-native research-log hierarchies.
- `research-log/themes/AGENTS.md`: compact router for routine theme-document maintenance instructions.
- `research-log/themes/instructions/`: sharded instructions for operations and file types.
- `research-log/scripts/pyrun`: minimal Python command resolver for entry examples.

## Working Model

Research-log conventions should remain flexible because researchers and projects organize work differently. The research log should not require one fixed top-level location.

A promising pattern is a theme document plus a matching folder. The theme document is a first-class living summary. The theme folder carries the timeline index, dated evidence entries, and entry-specific artifacts.

The theme-document pattern's contribution is not the folder location itself. Its contribution is how the hierarchy is implemented so agents can retrieve context efficiently, distinguish the living summary from historical evidence, preserve provenance, and update summaries without loading everything.

See `research-log/themes/README.md` for the current reusable pattern and `research-log/themes/AGENTS.md` for routine maintenance routing.

## Starter Requests

To create a new research-log theme, use a prompt such as:

- `Create a research-log theme for <theme>.`
- `Start a new research-log for <theme>.`
- `Set up <theme> as a research-log.`

To upgrade an existing source document into a research-log theme, use:

- `Upgrade <path-to-source-document> into a research-log theme using astro-agents.`

To continue an existing theme when the downstream repo has local `Research Logs` routing in `AGENTS.md`, use natural prompts such as:

- `Let's work on <theme>.`
- `Capture this in <theme>.`
- `Update the <theme> summary.`

`<theme>` should be a path to `<theme>.md`, `<theme>/`, a named theme from repo-local `Research Logs` routing, or a theme that is contextually obvious from the chat.

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
- Let prompt files and templates emerge from documented practice rather than creating them first.

## Deferred

- No final research-log schema is defined here yet.
