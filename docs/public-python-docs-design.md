# Public Python Docs Design

This document is the human-facing design reference for adding shared review support for the public documentation surface of Python projects.

Use it when designing or revising shared validation prompts for public Python package documentation.

It records the external guidance consulted, the main findings that matter for prompt design, and the derived design rules that the new review prompt should follow.

## Purpose

Use this document to define:

- which file classes and generated inputs belong to the default public documentation surface for a Python project
- which public documentation surface elements are always expected, conditionally required, or recommended for mature packages
- when docstrings, docs-generation config, examples, and docs-related tests become documentation-review inputs
- how public reachability should determine the default review scope for files under `docs/`
- which external sources justify the review rules for package metadata, docs reachability, and documentation structure

Use `docs/usage.md` for the shared repo-application model in general. Use this document when the task is specifically to design public-project documentation review support.

## Raw Findings

### Packaging And Public Metadata

- In Python packaging guidance, the README is not only a repo overview. It is also the long project description shown on PyPI through the `readme` field in `[project]`. [1][2]
- The one-line `description` field and the longer `readme` field in `pyproject.toml` are both part of the public presentation of the package. [2]
- The `[project.urls]` table is part of the public package surface. Packaging guidance explicitly shows `Homepage`, `Documentation`, `Repository`, `Issues`, and `Changelog` as normal project URLs. [3]
- Packaging guidance encourages meaningful, well-known URL labels because package indices and other metadata consumers may render those labels with special semantics. [3]

### Docs Site Structure And Reachability

- MkDocs treats `docs/` plus `mkdocs.yml` as the normal source tree for published docs. [4]
- By default, MkDocs renders Markdown files in the documentation directory into the built site. [4]
- The `nav` configuration defines which pages are included in the global site navigation and how that navigation is structured. [4]
- When `nav` is absent, MkDocs still discovers pages from the documentation directory automatically. [4]

### Documentation Architecture

- Read the Docs recommends using an explicit documentation structure rather than inventing one ad hoc from product features. [7]
- External documentation-structure guidance distinguishes between onboarding material, task guidance, reference material, and explanation as different documentation jobs. [5][6][7]
- Reference documentation is the natural home for APIs, classes, functions, and other technical descriptions. [6]

### Generated API Docs

- Auto-generated reference is not sufficient for the whole documentation system when a project also needs onboarding, task guidance, or explanatory material. This is a design conclusion drawn from the distinction between documentation roles in the external sources rather than a direct quoted rule. [5][6][7]
- At the same time, generated API documentation can be valuable because it stays faithful to the code. This is a design conclusion drawn from common generated-reference practice and the external guidance on reference material. [6]
- When a public docs page is generated from docstrings, the docstrings are part of the real source of truth for that published reference surface even if the built page is not reviewed directly. This is an inference from MkDocs-style generated-doc workflows plus the general distinction between reference material and other docs roles. [4][6]

## Public Documentation Surface

For the purposes of shared validation in this repo, the public documentation surface of a Python project is the set of public-facing documentation entrypoints, published docs sources, and documentation-generation inputs that define what external users and contributors actually see.

This surface normally includes:

- `README.md`
- public package metadata in `pyproject.toml` that affects package presentation or public documentation discovery
- public docs source files under `docs/` when the project publishes docs
- docs-site configuration such as `mkdocs.yml` when it determines published navigation or reachability
- `CONTRIBUTING.md` when the project invites external contribution
- `CHANGELOG.md` or equivalent release-history docs when release history is public
- `LICENSE` when a public entrypoint links to it directly
- examples or tutorial assets when the public docs rely on them

This definition is derived mainly from Python packaging guidance on public package metadata, MkDocs guidance on docs sources and navigation, and broader external guidance on documentation structure and reference material. [2][3][4][5][6][7]

This surface includes generated-doc inputs only when they materially define published docs, for example:

- docstrings that generate a public API reference page
- config that determines which modules or objects appear in generated docs
- tests or scripts that verify published examples, README snippets, or docs drift

This surface does not automatically include:

- every file under `docs/`
- built site output such as `site/`
- all source code
- all tests

## Reachability Rule

The default review scope for public docs should be the publicly reachable documentation graph rather than the full `docs/` tree.

This reachability rule is derived from MkDocs publication behavior and navigation structure rather than from a rule that every file under `docs/` is equally public. [4]

Start from these public entrypoints:

- `README.md`
- docs-site navigation and discovery configuration such as `mkdocs.yml`
- `CONTRIBUTING.md`
- `CHANGELOG.md`
- `LICENSE` when a public entrypoint links to it
- public package metadata entries that point users toward documentation or related public surfaces

Then review:

- docs pages linked from those entrypoints
- docs pages included in docs-site navigation
- docs pages implicitly published by docs-site discovery when the site configuration would publish them
- generated API-doc inputs only for the published reference surface that those entrypoints expose

Ignore by default:

- unlinked planning notes under `docs/`
- draft pages not included in public navigation or reachable linking
- generated build output

This rule is intended to keep under-development documents out of scope without requiring explicit exclusion markers.

## Required, Conditional, And Recommended Surface Elements

The review model should use tiers rather than a single mandatory checklist.

### Always Expected

- `README.md` as a public entrypoint
- consistent public package metadata in `pyproject.toml` for the package description and README binding
- consistent package URLs when the project publishes links for public docs, source, issues, or changelog history

These expectations come mainly from Python packaging guidance on README, metadata, and well-known project URLs. [1][2][3]

### Conditionally Required

Treat these as required when the repo clearly exposes or claims the corresponding surface:

- `docs/` source pages and docs-site config when the project publishes a docs site
- generated API reference inputs when the project publishes generated API docs
- `CONTRIBUTING.md` when the project invites outside contribution or documents contributor workflow publicly
- `CHANGELOG.md` or equivalent when release history is part of the public package surface
- `LICENSE` when `README.md` or another public entrypoint explicitly directs readers to it
- examples, notebooks, or tutorial assets when README or docs instruct users to rely on them

These conditions are design conclusions drawn from the principle that exposed public surfaces should be reviewed through their real sources of truth. The packaging and docs-site parts are grounded directly in the packaging and MkDocs sources; the contributor, changelog, and example cases are operational extensions of that principle.

### Recommended For Mature Public Packages

- clearer separation between onboarding material, task guidance, reference material, and explanatory content where the docs set is large enough to justify it
- explicit docs navigation rather than accidental file-order discovery
- drift checks that verify README snippets, examples, changelog/release coupling, or docs-generation integrity
- a stable contributor-facing path from `README.md` or package metadata into the docs system

The documentation-role recommendation is informed by the external documentation-structure sources. [5][6][7] The navigation recommendation comes from MkDocs. [4] The drift-check recommendation is an operational design choice rather than a direct external requirement.

## Review-Lens Implications

The future shared review prompt should evaluate public Python docs against the following design rules.

### Public Entry And Discovery

- `README.md` should work as a package entrypoint, not only as an internal repo overview.
- Public metadata and public docs should not point to missing, stale, or contradictory surfaces.
- Public docs should be discoverable through reachable links or published navigation.

### Source-Of-Truth Ownership

- `README.md`, docs pages, metadata, docstrings, and contributor/release docs should have clear owners for each kind of information.
- The same public instruction should not drift inconsistently across README, docs pages, examples, and metadata.
- Generated reference pages should be reviewed through their real sources of truth, not only through their rendered output.

### Generated API Reference

- If a published API reference is generated from docstrings, those docstrings are in scope for documentation review.
- Reviewing docstrings in this case is still documentation review, not generic code review.
- Generated API reference should be judged as reference material: authoritative, descriptive, and consistent with the public interface it exposes.

### Documentation Architecture

- Public docs should be reviewed as a user-facing documentation system, not only as a set of isolated files.
- Public docs should have clear role boundaries between onboarding material, task guidance, reference material, and explanatory content when the docs set is large enough to justify that separation.

The role-clarity point is informed by the external documentation-structure sources. [5][6][7]

### Contributor And Release Surfaces

- `CONTRIBUTING.md` and `CHANGELOG.md` should be treated as part of the public docs surface when they are public-facing.
- These documents should align with the public workflow the repo actually exposes through metadata, docs, and release mechanics.

## Prompt-Design Consequences

The shared validation implementation that follows from this document should:

- add a dedicated shared review path for public Python documentation surface review rather than silently broadening the existing generic document-writing review
- discover public entrypoints first, then traverse the reachable public docs graph
- inspect docs-generation inputs only when they materially define reachable public docs
- prefer source docs and generation inputs over reviewing built site output
- keep findings documentation-centered even when metadata, tests, examples, or docstrings are inspected as evidence
- use tiered findings so that missing recommended surfaces are softer than missing exposed or claimed surfaces

## References

1. Python Packaging User Guide, [How to make a PyPI-friendly README](https://packaging.python.org/en/latest/guides/making-a-pypi-friendly-readme/)
2. Python Packaging User Guide, [Writing your `pyproject.toml`](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
3. Python Packaging User Guide, [Well-known Project URLs in Metadata](https://packaging.python.org/en/latest/specifications/well-known-project-urls/)
4. MkDocs, [Writing your docs](https://www.mkdocs.org/user-guide/writing-your-docs/)
5. Diataxis, [The map](https://diataxis.fr/map/)
6. Diataxis, [Reference](https://diataxis.fr/reference/)
7. Read the Docs, [How to structure your documentation](https://docs.readthedocs.com/platform/latest/explanation/documentation-structure.html)
