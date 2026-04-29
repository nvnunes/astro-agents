# Public Python Projects

This document is a shared recommendation for downstream public Python projects that
use `astro-agents`.

Use it when deciding the project-level document set, source-of-truth boundaries,
public agent surface, and the shared `public-python` documentation-surface
model used by validation.

Use `docs/usage.md` for how to include this guidance in a downstream project.

Keep commands, package boundaries, persisted contracts, lifecycle rules,
deployment rules, and project-specific exceptions in project-local docs.

## Minimum Document Set

Every nontrivial public Python project should provide a small minimum document set
that helps both humans and agents work effectively.

Recommended minimum:

- `AGENTS.md`
  - the operational working brief for agents
- `README.md`
  - the human-facing overview, setup starting document, and orientation
    document
- `docs/architecture.md` or an equivalent design document
  - the source of truth for system structure, boundaries, ownership, and stable
    design assumptions
- `docs/testing.md` or an equivalent verification document
  - the source of truth for canonical test commands and completion expectations

For projects with local bootstrap, environment setup, hooks, or a recurring
development toolchain, also add:

- `docs/development.md`
  - the source of truth for local bootstrap, environment, hook, and daily
    workflow commands

If a project is still too small to justify separate documents, keep the minimum
necessary guidance in `README.md` and `AGENTS.md`. Split it out once the
content becomes reusable, stable, or operationally important.

Larger projects may also add project-local prompts under `agents/` and long-lived
supporting docs under `docs/` when those materials are stable enough to justify
their own source-of-truth location.

## Source Of Truth Boundaries

Prefer these document roles unless the project has a strong reason to use
different names.

- `AGENTS.md`
  - short operational guidance, routing and workflow, immediate constraints,
    source-of-truth references, and any shared-guidance references the project has
    chosen to adopt
- `README.md`
  - public overview, setup starting points, and where to go next
- `docs/architecture.md`
  - package shape, ownership boundaries, persisted contracts, data or lifecycle
    rules, and public API or CLI design
- `docs/testing.md`
  - canonical verification commands, completion expectations, and testing-scope
    boundaries
- `docs/development.md`
  - bootstrap, local environment setup, hooks, toolchain conventions, and daily
    development commands
- `docs/<topic>-plan.md` or `docs/plan.md`
  - transitional context, phased implementation, migration sequencing, or work
    grouping
- `docs/api.md` or other narrower reference docs
  - stable public interfaces when `docs/architecture.md` would otherwise become
    overloaded
- `docs/glossary.md`
  - recurring local terms when the project depends on stable term meanings or term
    ownership
- `docs/data-sources.md`
  - durable data artifacts the project consumes, produces, ships, or expects users
    or agents to work with

Keep long background explanation out of `AGENTS.md`. Link to local
source-of-truth docs instead.

Split guidance into a separate source-of-truth doc when:

- it is substantial enough to need its own stable owner
- it is explanatory rather than operational
- it needs to stay stable across many tasks
- it would otherwise be repeated across multiple files
- the project has recurring local terms that justify a dedicated glossary
- the project has enough complexity that humans and agents need a persistent
  source of truth

Do not use `docs/data-sources.md` as the owner for:

- CLI or API input grammar
- normalization rules
- persisted schema contracts
- field-level interface semantics

When a project needs a stable source of truth for data contracts or persistence
rules, use a more explicit owner such as `docs/architecture.md`, `docs/api.md`,
or a narrower document whose name makes that contract role clear.

## Naming And Cross-Linking

Prefer stable, predictable document names when they fit the project:

- `README.md`
- `AGENTS.md`
- `docs/architecture.md`
- `docs/development.md`
- `docs/glossary.md`
- `docs/testing.md`
- `docs/<topic>-plan.md`
- `docs/<topic>-design.md`

These names are not required, but predictable names make documents easier for
both humans and agents to find.

When these documents exist:

- make long-lived source-of-truth docs discoverable from `AGENTS.md`,
  `README.md`, or another clear starting document
- make the role of each doc explicit near the top, especially whether it is
  operational guidance, project-local source of truth, or shared recommendation
- keep cross-references direct and current when doc names, paths, or ownership
  change
- avoid scattering the same instruction across multiple files without a clear
  owner

## Agent Surface

For a public project, keep its agent surface from depending too heavily on
user-specific global prompting, particularly when other contributors are
expected.

- keep project-specific guidance visible inside the project's own agent surface
- do not hardcode absolute paths to a private workspace prompt library
- use a generic bootstrap line such as `Use the shared astro-agents prompt
  library for reusable authoring, review, and routing guidance.`
- when the project adopts shared recommendation docs from `astro-agents`, mention
  them explicitly rather than assuming they will be rediscovered indirectly

This allows a user to keep a reusable shared prompt library without baking
private path assumptions or user-specific bootstrap structure into public
projects.

## Shared Public-Python Docs Review Model

This section is the narrative owner for the shared `public-python`
documentation-surface model used by validation.

### Public Documentation Surface

For public Python projects, the public documentation surface is the set of
public-facing starting documents, published docs sources, and
documentation-generation inputs that define what external users and
contributors actually see.

This surface normally includes:

- `README.md`
- public package metadata in `pyproject.toml` that affects package
  presentation or public documentation discovery
- public docs source files under `docs/` when the project publishes docs
- docs-site configuration such as `mkdocs.yml` when it determines published
  navigation or reachability

Treat these as part of the public documentation surface when the public entry
surface exposes or depends on them:

- generated API-doc inputs such as docstrings and docs-generation config
- `CONTRIBUTING.md`
- `CHANGELOG.md` or equivalent release-history docs
- `LICENSE` when a public starting document links to it directly
- examples, notebooks, or other tutorial assets
- docs-related tests or scripts that verify public examples, README snippets,
  or docs drift

This surface includes generated-doc inputs only when they materially define or
verify published docs, for example:

- docstrings that generate a public API reference page
- config that determines which modules or objects appear in generated docs
- tests or scripts that verify published examples, README snippets, or docs
  drift

This surface does not automatically include:

- every file under `docs/`
- built site output such as `site/`
- all source code
- all tests

### Reachability Rule

The default review scope for public docs should be the publicly reachable
documentation graph rather than the full `docs/` tree.

Start from these public starting documents:

- `README.md`
- docs-site navigation and discovery configuration such as `mkdocs.yml`
- `CONTRIBUTING.md`
- `CHANGELOG.md`
- `LICENSE` when a public starting document links to it
- public package metadata entries that point users toward documentation or
  related public surfaces

Then review:

- docs pages linked from those starting documents
- docs pages included in docs-site navigation
- docs pages implicitly published by docs-site discovery when the site
  configuration would publish them
- generated API-doc inputs only for the published reference surface that those
  starting documents expose

Ignore by default:

- unlinked planning notes under `docs/`
- draft pages not included in public navigation or reachable linking
- generated build output

Treat docstrings, docs-generation config, examples, and docs-related tests as
documentation-review inputs only when they materially define or verify
reachable public docs.

### Surface Tiers

Use tiers rather than a single mandatory checklist when judging the public-docs
surface.

#### Always Expected

- `README.md` as a public starting document
- consistent public package metadata in `pyproject.toml` for the package
  description and README binding
- consistent package URLs when the project publishes links for public docs,
  source, issues, or changelog history

#### Conditionally Required

Treat these as required when the project clearly exposes or claims the
corresponding surface:

- `docs/` source pages and docs-site config when the project publishes a docs
  site
- generated API reference inputs when the project publishes generated API docs
- `CONTRIBUTING.md` when the project invites outside contribution or documents
  contributor workflow publicly
- `CHANGELOG.md` or equivalent when release history is part of the public
  package surface
- `LICENSE` when `README.md` or another public starting document explicitly
  directs readers to it
- examples, notebooks, or tutorial assets when `README.md` or docs instruct
  users to rely on them

#### Recommended For Mature Public Packages

- clearer separation between onboarding material, task guidance, reference
  material, and explanatory content where the docs set is large enough to
  justify it
- explicit docs navigation rather than accidental file-order discovery
- drift checks that verify README snippets, examples, changelog or release
  coupling, or docs-generation integrity
- a stable contributor-facing path from `README.md` or package metadata into
  the docs system

### Review Lens

Review the public-Python documentation surface against these principles:

#### Public Entry And Discovery

- `README.md` should work as a package starting document, not only as an
  internal project overview
- public metadata and public docs should not point to missing, stale, or
  contradictory surfaces
- public docs should be discoverable through reachable links or published
  navigation

#### Source-Of-Truth Ownership

- `README.md`, docs pages, metadata, docstrings, and contributor or release
  docs should have clear owners for each kind of information
- the same public instruction should not drift inconsistently across `README`,
  docs pages, examples, and metadata
- generated reference pages should be reviewed through their real sources of
  truth, not only through their rendered output

#### Generated API Reference

- if a published API reference is generated from docstrings, those docstrings
  are in scope for documentation review
- reviewing docstrings in this case is still documentation review, not generic
  code review
- generated API reference should be judged as reference material:
  authoritative, descriptive, and consistent with the public interface it
  exposes

#### Documentation Architecture

- public docs should be reviewed as a user-facing documentation system, not
  only as a set of isolated files
- public docs should have clear role boundaries between onboarding material,
  task guidance, reference material, and explanatory content when the docs set
  is large enough to justify that separation

#### Contributor And Release Surfaces

- `CONTRIBUTING.md` and `CHANGELOG.md` should be treated as part of the public
  docs surface when they are public-facing
- these documents should align with the public workflow the project actually
  exposes through metadata, docs, and release mechanics

### Prompt-Design Consequences

The shared validation implementation that follows from this model should:

- add a dedicated shared review path for public Python documentation surface
  review rather than silently broadening the existing generic document-writing
  review
- discover public starting documents first, then traverse the reachable public
  docs graph
- inspect docs-generation inputs only when they materially define reachable
  public docs
- prefer source docs and generation inputs over reviewing built site output
- keep findings documentation-centered even when metadata, tests, examples, or
  docstrings are inspected as evidence
- use tiered findings so that missing recommended surfaces are softer than
  missing exposed or claimed surfaces

### Design Rationale

This model is grounded in a small set of external documentation and packaging
ideas:

- Python packaging guidance treats `README.md`, `[project]` metadata, and
  `[project.urls]` as part of the public package presentation
- MkDocs guidance supports treating docs sources, navigation, and docs
  discovery behavior as part of the published documentation surface
- Diataxis and Read the Docs support evaluating larger doc sets as a structured
  documentation system rather than as one undifferentiated tree
- generated API reference remains documentation, but its real sources of truth
  may be docstrings or generation config rather than only rendered pages

## References

1. Python Packaging User Guide, [How to make a PyPI-friendly README](https://packaging.python.org/en/latest/guides/making-a-pypi-friendly-readme/)
2. Python Packaging User Guide, [Writing your `pyproject.toml`](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
3. Python Packaging User Guide, [Well-known Project URLs in Metadata](https://packaging.python.org/en/latest/specifications/well-known-project-urls/)
4. MkDocs, [Writing your docs](https://www.mkdocs.org/user-guide/writing-your-docs/)
5. Diataxis, [The map](https://diataxis.fr/map/)
6. Diataxis, [Reference](https://diataxis.fr/reference/)
7. Read the Docs, [How to structure your documentation](https://docs.readthedocs.com/platform/latest/explanation/documentation-structure.html)
