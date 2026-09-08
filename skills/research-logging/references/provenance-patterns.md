# Provenance Patterns

Use this index after the material-input instructions when a Record workflow
contains artifact, command, or provenance work. These are common cases, not an
exhaustive taxonomy or a whitelist. Load only the cards that match the current
workflow:

- Default named producers and consumers:
  `references/provenance-patterns/named-artifacts.md`.
- Whole-directory production or consumption:
  `references/provenance-patterns/directory-artifacts.md`.
- Explicit bounded identity for a very large directory:
  `references/provenance-patterns/selected-directory-identity.md`.
- Existing large simulation results produced outside the log:
  `references/provenance-patterns/external-simulation-origin.md`.
- Researcher-authored configuration outside `data/`:
  `references/provenance-patterns/configuration-origin.md`.
- Exact committed source as an input:
  `references/provenance-patterns/commit-input.md`.
- Reuse of one generated artifact in a later same-log entry:
  `references/provenance-patterns/cross-entry-reuse.md`.
- Supporting material retained outside presented-evidence provenance:
  `references/provenance-patterns/retained-support.md`.

`LOG_TOOL` in the cards is this skill's `scripts/log` launcher and `LOG` is the
logical log base. Tooling calls author the record and stay outside the entry's
research command blocks. Research commands run from the owning entry. Prefer
`--input-*` and `--output-*` script options; use explicit runner role selectors
only when the real interface cannot be changed. Artifact tokens name resources
but do not assign direction.
