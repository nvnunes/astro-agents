# Review Operation Instructions

Use this file when the user asks to review a research log's structure,
consistency, synthesis, or writing.

`Review` is report-first and does not own log edits. Complete findings before
any separately authorized fix. Route entry changes through Record or Replace,
summary changes through Update Summary, and a persistent review record through
Record.

## Scope

Infer the focus from the request:

- named passages or entries: review only those targets
- writing or style: review prose and information organization
- structure or consistency: review integrity and lifecycle conventions
- an unqualified log review: combine writing and integrity checks

Load only the references needed for that focus.

## Review Checks

For writing, check `references/research-log-writing.md`,
`references/file-entry-labels.md`, `references/file-summary.md`, and
`references/file-summary-ai-use.md` as applicable. Look
for evidence loss, status inflation, unsupported interpretation, stale
synthesis, weak comparison structure, misplaced labels, invented validation,
missing or empty summary-level AI use disclosures, disclosure wording changed
without researcher direction, obsolete entry-level `AI Use:` labels,
paragraph-heavy summaries where bullets would be clearer, avoidable
meta-introductions, bullets that combine separable claims, unnecessary artifact
inventories, indirect or redundant evidence presentation,
artifact-management narration, resolved TODOs, obsolete provisional markers,
non-retained material, and work-log narration unrelated to research evidence.

Classify every `##` entry section as experimental, synthesis, or prose under
`file-entry-labels.md`. Report missing required labels, incompatible label
mixtures, unsupported labels, experiments presented as synthesis or prose, and
broader cross-source synthesis embedded in an experimental section rather than
grounded in that section's `Results:`. Review owns these findings. A validation
skip confirms only that the section was structurally ambiguous; it does not
replace review or decide the intended repair.

For evidence presentation, apply
`references/file-presented-evidence.md`, including its
review boundary.

Also check semantically that each substantive summary point is supported by an
entry. Keep this review bounded to presentation and summary-to-entry support.
Do not decide whether the method, evidence, interpretation, or conclusion is
scientifically correct. Do not adjudicate presented-item equivalence or a
complete provenance chain. Check only whether required commands, input
references, artifacts, and evidence-association records are structurally
present and correctly formatted; validation determines whether they establish
the presented evidence and its provenance.

For integrity, check the core shape in this skill's `SKILL.md` and the
applicable `references/file-entry-naming.md`, `references/file-entry.md`,
`references/file-summary.md`, `references/file-references.md`,
`references/file-script.md`, `references/file-entry-commands.md`, and
`references/file-data-index.md`. When retention exists, also read
`references/file-retention.md`. Look for
broken or stale links, unresolved citation keys, invalid entry paths or IDs,
summary-entry inconsistency, unresolved `pyrun` or `data.json` references, and
missing required provenance declarations.

Do not require routine production checks to be narrated or run commands.
Report only missing research-relevant methods, visible quality problems, and
reconstruction limits supported by the record.

Also look for:

- entry-only code at log or project scope, or multi-entry code copied into entries
- recorded commands broken by changes to log-level or project-level shared code
- active-work Python commands that do not invoke `./pyrun` without a recorded,
  researcher-approved symlink exception
- missing or non-symlink entry-root `pyrun` for active-work Python commands
  without that exception
- copied or vendored `pyrun` files
- project API choices that affect what the evidence establishes but are not recorded
- interpreter fallback used despite a declared project environment without researcher approval
- duplicate `data.json` names or targets, items unused by recorded `<name>`
  tokens, missing input items, raw-path token bypasses, fingerprint drift,
  conflicting origin boundaries, unresolved tokens, or raw absolute and
  outside-entry input paths that should use `<name>`
- active-work figures without a generating command in the document that presents them
- active-work plot scripts that accept missing cases, non-finite values, or incompatible units
- saved figures with evidence-affecting defects
- serialized-input code that does not check its expected structure
- runtime caches not covered by project ignore rules, including `__pycache__/`, `.pytest_cache/`, and `.ruff_cache/`

Treat direct existing decisions as researcher decisions unless explicitly
marked proposed, provisional, or agent-generated. Pending validation alone does
not make a researcher decision provisional. A structural review may recommend
splitting when distinct topics impair retrieval or maintenance; length alone is
not a reason. Report the recommendation without reorganizing the log unless the
researcher separately requests or approves the change.

## Output

Return numbered findings ordered by importance. Distinguish direct violations
from optional improvements. For each finding, cite the affected location,
explain the applicable rule and why it matters, and suggest a corrective
direction. If no material findings exist, say so and name any residual risk or
unverified area.

During an authorized follow-on operation, preserve source wording unless the
fix requires a specific rewrite. Do not omit, condense, paraphrase, or
materially rewrite source content without explicit direction.
