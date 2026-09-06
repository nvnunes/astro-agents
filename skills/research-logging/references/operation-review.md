# Review Operation Instructions

Use this operation only when the researcher explicitly requests Review or
clearly directs a semantic judgment represented by a named lens or review
group. Semantic Review is costly. Never add it to Record, Replace, Update
Summary, Repair, Reorganize, Validate, reproduction, or ordinary completion
checks on your own initiative.

Do not select review lenses for a request to inspect, explain, triage, or
determine the cause of named mechanical-validation findings. Route that work
to Validate's read-only diagnosis path even when it requires inspecting
commands, artifacts, or research metadata.

Review is report-first and read-only with respect to the maintained research
account. Report findings and stop. A later researcher instruction to address
findings authorizes a new owning research operation; the same agent may switch
roles and perform that work.

## Select The Review

Read `references/review-lenses/catalog.md`. Route the request by meaning rather
than through a keyword table.

- Apply explicitly named lenses or groups directly.
- When natural wording maps clearly to one or more catalog descriptions, name
  the selected lenses or group briefly and proceed without confirmation.
- Select every concern clearly requested, but do not add adjacent lenses merely
  because they could be useful.
- Route “review the analysis,” “review the evidence,” and “review the record”
  to Analysis Review, Evidence Review, and Record Review, respectively.
- Route an explicit complete semantic review or request to review everything to
  all three groups.
- For an unqualified request such as “review this research log,” present the
  catalog's four-option group-first menu and ask which option or options to use.
- When a focused request remains ambiguous outside the named groups, present
  the complete numbered lens catalog verbatim and ask which lenses to use.

Treat `Validate`, `Review`, and `Reproduction` as strong operation signals, but
interpret the complete request. Researchers need not say “mechanical” to mean
Validate. Treat a bare request to check or verify a research log as ambiguous
and ask which of the three operations the researcher wants. A stated semantic
question may still route directly to its matching lens by meaning.

After selection, read only the chosen files under
`references/review-lenses/`. A named group expands to its constituent lens
files. For Research-Log Writing, also apply `$science-writing`; keep its use
bounded by the selected lens prompt.

## Resolve Target And Scope

Resolve the review target separately from the lens selection. The target may be
a passage, claim, evidence presentation, figure, table, script, artifact,
entry, summary, complete log, or an explicitly named collection.

- When a maintained `<log>.md` file identifies the research log, the complete
  log target includes that summary and its sibling `<log>/` tree. Discover the
  tree independently of summary navigation because incomplete navigation may
  itself be a finding. Inspect only the material needed by the selected lenses.
- Apply findings only to the requested target.
- Inspect supporting material only when a selected lens requires it.
- Do not turn inspected context into a wider review.
- Use the narrowest reasonable target when context resolves it. Ask when the
  target has materially different plausible interpretations.
- Report an incidental concern outside the target as an unreviewed,
  out-of-scope note rather than expanding Review.

## Conduct The Review

Build only the context required by the target and selected lenses. Reuse
factual discovery across lenses without treating one lens's judgment as proof
of another's.

- Organize work around the material. While an entry, claim, evidence item,
  source, artifact, or script is in context, apply every selected lens for
  which it is relevant.
- Use another traversal only when a materially different unit, order, or body
  of material requires one, such as external-source inspection, rendered-
  presentation inspection, or code and dependency tracing.
- Keep shared context ephemeral. Do not create a review cache, context packet,
  ledger, status file, or log record.
- Use read-only or disposable diagnostics only to inspect existing material.
  Do not execute recorded research commands, vary research inputs or methods,
  regenerate artifacts, or create new evidence.
- Do not run Validate or reproduction as part of Review. Existing generated
  reports may be read as context without inheriting their status.
- Use external lookup only when the selected lens requires external material.

Require a fresh task only when the researcher requests or the report claims an
independent review. Give that task the request, resolved target, selected lens
prompts, current material, and neutral locators or access notes. Exclude prior
semantic findings, expected conclusions, proposed corrections, and authoring
discussion unless the requested target is comparison or adjudication of those
materials.

## Preserve Authority Boundaries

Review may conclude that the target has a problem under a selected lens. It may
not decide which conclusion, interpretation, method, evidence selection,
decision, or research direction the maintained account should accept.

Treat direct existing decisions as researcher decisions unless explicitly
marked proposed, provisional, or agent-generated. Do not invent evidence,
results, uncertainty, references, decisions, or conclusions to fill a gap.

Review writing inside a research log through Research-Log Writing and its
science-writing pairing. Use science-writing directly for scientific prose
outside a research log. Use code-quality review for general software quality;
use Implementation Fidelity only for correspondence between research code and
the recorded method. Use reference work to find, acquire, register, or update
references; Citation Support only judges an attribution.

If a request explicitly spans separate review systems, apply each while keeping
their criteria and findings distinct. Ask when a request such as “review this
script” could reasonably mean code quality, Implementation Fidelity, Result
Derivation, or Numerical Validity.

## Report Findings

Return one self-contained report that names the target, applied lenses and
groups, whether requested independence was satisfied, and any non-obvious scope
interpretation. A group is only a selection convenience; it receives no shared
status or verdict.

Use only these finding classes:

- **Issue:** a supported problem requiring correction, a researcher decision,
  or additional research.
- **Improvement:** an optional change that would strengthen clarity,
  usefulness, or robustness without correcting a demonstrated problem.
- **Unverified:** a judgment that could not be completed because necessary
  material, access, expertise, or scope was unavailable.

Number findings by material consequence. Give each a compact heading containing
the class, owning lens or lenses, title, and durable affected location. Follow
with one readable description that identifies the support or absence, explains
the reasoning and consequence, and ends with a short recommended action. Do not
use separate `Basis`, `Consequence`, or similar field labels, and do not assign
a universal severity. Verify that every reported file path and line locator
names the material actually inspected.

If no material issues are found, say that no material issues were found within
the named target and applied lenses. List unverified areas and out-of-scope
concerns separately. Do not report semantic `PASS`, approval, certification, or
durable review status.

After reporting, stop Review. Do not edit the log or begin a recommended action
until the researcher instructs you to address findings. A later instruction
authorizes only the findings and corrected state it identifies; ask when that
scope remains ambiguous.
