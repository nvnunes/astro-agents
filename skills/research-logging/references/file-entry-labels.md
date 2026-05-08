# Entry Label Instructions

Use this file when adding, revising, or reviewing structured labels inside entry prose.

Entries can use labels to provide structure that helps an agent parse the document. Recommended labels, in a common workflow order, are:

- `Background:` question, hypothesis, motivation, prior state, or conditions that matter.
- `Steps:` commands, scripts, searches, source lookups, comparisons, analytical steps, prompts, parameters, inputs, or other steps needed to reproduce or understand the results.
- `Results:` generated data, tables, figures, files, measurements, source findings, intermediate notes, candidate interpretations, or other results.
- `Observations:` what the researcher noticed in the results or evidence, including patterns, contradictions, interpretations, or notable absences.
- `Validation:` checks used to decide whether the observation or result should be trusted, including reproducibility checks, review of code written by AI, and checks against evidence, references, assumptions, or alternatives.
- `Uncertainty:` what remains unknown or limits confidence in the result, observation, validation, or decision, including assumptions, limitations, caveats, or confidence limits.
- `Decisions:` what was chosen, rejected, deferred, treated as current, or accepted as the working interpretation.
- `Follow-up:` unresolved work intentionally marked for the log-level `## Follow-ups` section.
- `AI Use:` agent involvement that affects evidence, results, validation, uncertainty, or decision context.

Labels are local to the section where they appear unless they clearly describe the whole entry.

Use labels only when they accurately describe the local content. Do not add a label just to match the recommended list, and do not force content into a label that changes or blurs its meaning. If no recommended label fits cleanly, use an ordinary heading or prose instead.
