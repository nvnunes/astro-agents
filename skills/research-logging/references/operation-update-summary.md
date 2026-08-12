# Update Summary Operation Instructions

Use this file only when the researcher asks for or approves an update to
current understanding or `## Follow-ups` in `<log>.md`.

`Update Summary` synthesizes entries into the current summary rather than
restating them chronologically. Creating, recording, reorganizing, or
validating may require narrow changes to entry links or the generated
validation snapshot; those changes do not authorize revisions to current
understanding or `## Follow-ups`.

Read `skills/research-logging/references/file-summary.md` and
`skills/research-logging/references/file-presented-evidence.md` for summary
guidance.

## Behavior

Update the canonical `<log>.md` file with the normal repository editing tools.
Research and validation are assumed not to modify the same log concurrently.
The validator's generated snapshot uses the repository validation lock and
an atomic replacement; ordinary research summary edits need no second lock or
optimistic publication protocol.

- Read the relevant entry documents before changing summary claims.
- Preserve researcher-defined topic organization and framing. Normalize
  incidental prose to the summary format in `file-summary.md` when meaning is
  unchanged.
- Add or update links from summary claims to supporting entries where useful.
- Carry forward current retained understanding, explicitly retained provisional
  or planned state, evidence needed to explain current conclusions, and active
  follow-ups. Do not preserve superseded material merely to narrate history.
- Build `## Follow-ups` only from explicit entry `Follow-up:` items or
  researcher-requested log-level additions. Do not infer follow-ups from prose
  or update this section during Record.
- Keep detailed evidence, long caveats, commands, and reconstruction details in entries.
- Preserve the existing `## AI Use` disclosure exactly unless the researcher
  asks to revise it. Do not reset customized wording to the creation default.
- Preserve the complete `## Validation` section byte-for-byte. Do not open
  generated validation records solely because summary content changed.
- Do not add conclusions that are not supported by entries, references, or user-provided direction.
- If summary text appears stale, unsupported, or inconsistent with entries,
  either fix it when the requested task clearly authorizes that change or
  report the issue and ask before editing.
