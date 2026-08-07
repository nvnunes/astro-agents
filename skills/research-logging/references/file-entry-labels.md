# Entry Label Instructions

Use this file when adding, revising, or reviewing structured labels inside
entry prose.

Choose the local form from the section's role:

- **Information:** Use concise ordinary prose, with descriptive headings only
  when helpful. Do not force information without its own evidence into labels.
- **Performed experiment:** Use at minimum `Background:`, `Steps:`, `Results:`,
  `Validation:`, `Observations:`, and `AI Use:`.
- **Planned work in the current effort:** Use a concise stub with `Background:`
  and, when useful, anticipated `Steps:`. Keep future tense and omit empty
  result-oriented labels.

Labels are local to their descriptive `##` section. Put content under the
defined label it most closely matches rather than inventing a synonym. Do not
invent content to fill a label. Omit conditional `Decisions:`, `Uncertainty:`,
and `Follow-up:` labels when they have no content.

Write each label on its own line in inline code, for example `Background:`.

### `Background:`

State the question, motivation, prior state, hypothesis, and conditions needed
for interpretation. Keep commands, generated values, and conclusions elsewhere.

### `Steps:`

Record commands, scripts, source lookups, inputs, parameters, and analytical
actions needed to understand or reproduce the result. Keep procedure
proportional to its interpretive importance. Put motivation and methodological
rationale in `Background:` and generated outputs in `Results:`.

### `Results:`

Record generated measurements, tables, figures, files, and direct source
findings, not interpretation. Do not inventory figures already displayed or
files already named in commands; name a file separately only when the reader
otherwise cannot locate a needed artifact.

### `Validation:`

Use this required label to state validation status. Record validation only when
performed or explicitly confirmed by the researcher, stating what the
researcher checked and what the check supports. Otherwise write exactly
`- TODO: Researcher validation.` Never treat successful execution, tests, or
agent review as researcher validation.

### `Observations:`

Record evidence-grounded patterns, contradictions, notable absences, and
interpretations, keeping supporting results close enough to check. An agent may
draft observations when evidence and context support them; treat them as drafts
for researcher revision, not researcher-approved interpretations.

### `Uncertainty:`

Use this label rarely and only under researcher direction for uncertainty the
researcher intentionally leaves unaddressed while retaining the result or
decision. Do not use it for routine caveats, incomplete validation, or planned
work.

### `Decisions:`

When drafting, record only researcher decisions. When reviewing, treat direct
decisions as researcher decisions unless explicitly marked proposed,
provisional, or agent-generated. Preserve the evidence or constraint supplied
with the decision. Omit the label if no researcher decision exists.

### `Follow-up:`

Reserve this label for deferred work outside the current research effort that
the researcher intentionally wants carried into the log-level `## Follow-ups`.
Do not use it for current planned work or speculative ideas.

### `AI Use:`

Use `skills/research-logging/references/file-entry-ai-use.md` for agent
provenance. Write `None.` when no agent work had a surviving effect on the
research record. If an older experiment lacks reconstructable provenance,
report the omission rather than inventing a note or inserting a TODO.
