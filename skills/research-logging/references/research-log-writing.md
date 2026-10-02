# Research Log Writing

Use this file when drafting or revising research-log prose in entries or
summaries.

Research logs are scientific working records for the researcher. Present the
question, method, selected evidence, and reasoning needed to understand the
investigation. Describe the scientific account directly; omit maintenance
history, implementation progress, and accounts of how the agent assembled the
entry unless the researcher requests them.

## Style

- Lead each section with the scientific question or comparison that makes its
  evidence relevant. Include prior scientific work when it explains the
  motivation or interpretation.
- Organize repeated experiments around the question they answer. Keep the
  baseline, candidate, measured benefit, relevant cost, and tested boundary
  together; use tables or parallel bullets for shared comparison dimensions.
- State supported results plainly. State a researcher decision only when
  explicitly directed to record it. Name the quantity, baseline, and scope
  instead of relying on `better`, `faster`, `stable`, `accurate`, or `did not work`.
- Keep qualifiers beside the claims they limit. Distinguish intermediate-model
  differences from downstream or science-visible effects, and state what a
  threshold measures, why it matters, and what it does not establish.
- Prefer short declarative bullets in descriptive blocks. Use equations,
  tables, or brief connective prose when they communicate the argument more
  clearly. Keep structure shallow: use descriptive section headings and the
  standard block labels, without extra subheadings inside blocks or
  expand/collapse sections unless the researcher requests them.
- Develop mathematical explanations sequentially. Define quantities before
  use, reuse established notation, and show the relationships that connect
  assumptions to the result. Use prose to explain the equations' role rather
  than substitute for the mathematical argument.
- Present evidence directly. Never turn an entry into an agent diary or work
  log. Omit agent workflow and routine successful checks unless they affect the
  evidence.
- Select figures and tables for the scientific comparison they establish. Place
  each beside the argument it supports, and omit redundant presentations and
  inventories. Use the project's established plotting API and visual
  conventions when available. Put essential distinctions in labels, legends,
  or other plot encodings; avoid prose that merely teaches ordinary axis or
  colour reading.
- Retain negative evidence only when it still explains a result, decision, or
  useful lesson. State the discriminating result, rejection reason, and boundary
  of what was ruled out.
- Keep evidence, observation, inference, researcher validation, material
  limitation, and decision distinct. Use `retained`, `accepted`, and `validated`
  only when that status is established; otherwise use `proposed`,
  `recommended`, `provisional`, `planned`, or `awaiting validation` as
  appropriate.
- Keep a researcher operational decision even when validation or scientific
  uncertainty remains. State the limit separately without weakening the
  decision, and do not create an `Uncertainty:` section unless the researcher
  directs it.
- Never convert an agent recommendation or explicitly provisional decision into
  a researcher-accepted conclusion.

## Preservation

- Preserve the meaning of retained evidence, researcher validation and
  decisions, and intentionally retained uncertainty. Do not make prose sound
  more certain, complete, or researcher-endorsed merely to make it smoother.
- Preserve exact numerical values, units, variable names, commands, paths,
  citation keys, and stated uncertainty unless the source changes.
- Preserve the meaning and timing of retained evidence. When revising an
  explanation, remove obsolete wording within the authorized scope without
  presenting later evidence as previously known or rewriting earlier results
  to match a later conclusion.
- Surface conflicting or stale current-state claims rather than silently
  resolving them outside the active operation.
