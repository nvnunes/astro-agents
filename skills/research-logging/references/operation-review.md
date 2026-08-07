# Review Operation Instructions

Use this file when the user asks to review a research log's structure,
consistency, synthesis, or writing.

`Review` is report-first. Do not edit the log or create a review entry unless
the user explicitly asks for fixes or a persistent record.

## Scope

Infer the focus from the request:

- named passages or entries: review only those targets
- writing or style: review prose and information organization
- structure or consistency: review integrity and lifecycle conventions
- an unqualified log review: combine writing and integrity checks

Load only the references needed for that focus.

## Review Checks

For writing, check `skills/research-logging/references/research-log-writing.md`,
`skills/research-logging/references/file-entry-labels.md`,
`skills/research-logging/references/file-summary.md`, and
`skills/research-logging/references/file-summary-ai-use.md` as applicable. Look for
evidence loss, status inflation, unsupported interpretation, stale synthesis,
weak comparison structure, misplaced labels, invented validation, missing or
empty summary-level AI use disclosures, disclosure wording changed without
researcher direction, obsolete entry-level `AI Use:` labels, unnecessary
artifact inventories, indirect or redundant evidence presentation,
artifact-management narration, resolved TODOs, obsolete provisional markers,
and non-retained material.

For integrity, check the core shape in `skills/research-logging/SKILL.md` and
the applicable `skills/research-logging/references/file-entry-naming.md`,
`skills/research-logging/references/file-entry.md`,
`skills/research-logging/references/file-summary.md`,
`skills/research-logging/references/file-references.md`,
`skills/research-logging/references/file-script.md`,
`skills/research-logging/references/file-entry-commands.md`, and
`skills/research-logging/references/file-data-index.md` references. Look for
broken or stale links, unresolved citation keys, invalid entry paths or IDs,
summary-entry inconsistency, unresolved `pyrun` or `data.csv` references, and
missing required provenance. Also look for:

- entry-only code at log or project scope, or multi-entry code copied into entries
- recorded commands broken by changes to log-level or project-level shared code
- recorded Python commands that do not invoke `./pyrun` without a recorded, researcher-approved symlink exception
- missing or non-symlink entry-root `pyrun` for recorded Python commands without that exception
- copied or vendored `pyrun` files
- project API choices that affect what the evidence establishes but are not recorded
- interpreter fallback used despite a declared project environment without researcher approval
- duplicate `data.csv` names or rows unused by recorded `<name>` tokens
- retained figures without a generating command in the document that presents them
- plot scripts that accept missing cases, non-finite values, or incompatible units
- retained figures without recorded visual inspection
- serialized artifacts consumed later without reload validation
- runtime caches not covered by project ignore rules, including `__pycache__/`, `.pytest_cache/`, and `.ruff_cache/`

Treat direct existing decisions as researcher decisions unless explicitly
marked proposed, provisional, or agent-generated. Pending validation alone does
not make a researcher decision provisional. Suggest splitting only when
distinct topics impair retrieval or maintenance; length alone is not a reason.

## Output

Return numbered findings ordered by importance. Distinguish direct violations
from optional improvements. For each finding, cite the affected location,
explain the applicable rule and why it matters, and suggest a corrective
direction. If no material findings exist, say so and name any residual risk or
unverified area.

If the user asks to apply fixes, preserve source wording unless the fix requires
a specific rewrite. Do not omit, condense, paraphrase, or materially rewrite
source content without explicit direction.
