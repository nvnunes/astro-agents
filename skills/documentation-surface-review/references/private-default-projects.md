# Private-Default Projects

This reference is the private/default documentation surface model used by
`skills/documentation-surface-review/SKILL.md`.

Use it when deciding the project-level document set, source-of-truth boundaries,
private agent surface, and the shared `private-default` documentation-surface
model used by validation.

Keep commands, package boundaries, persisted contracts, lifecycle rules,
deployment rules, and project-specific exceptions in project-local docs.

## Minimum Document Set

Every nontrivial private or default-profile project should provide a small
minimum document set that helps humans and agents work from the same durable
facts.

Recommended minimum:

- `AGENTS.md`
  - the operational working brief for agents
- `README.md`
  - the project orientation, setup starting document, and where-to-go-next
    document
- `docs/architecture.md` or an equivalent design document
  - the source of truth for system structure, boundaries, ownership, and stable
    design assumptions
- `docs/testing.md` or an equivalent verification document
  - the source of truth for canonical checks, validation expectations, and the
    completion bar

For projects with local bootstrap, environment setup, hooks, or recurring
development tooling, also add:

- `docs/development.md`
  - the source of truth for local bootstrap, environment, hook, and daily
    workflow commands

If a project is still too small to justify separate documents, keep the minimum
necessary guidance in `README.md` and `AGENTS.md`. Split it out once the
content becomes reusable, stable, or operationally important.

Larger projects may also add project-local prompts or local review prompts when
recurring project-specific work needs an agent-facing owner.

## Source Of Truth Boundaries

Prefer these document roles unless the project has a strong reason to use
different names.

- `AGENTS.md`
  - short operational guidance, workflow, immediate constraints,
    source-of-truth references, and any shared-guidance references the project
    has chosen to adopt
- `README.md`
  - project orientation, setup starting points, and where to go next
- `docs/architecture.md`
  - system shape, ownership boundaries, persisted contracts, data or lifecycle
    rules, and supported public or internal interfaces
- `docs/testing.md`
  - canonical verification commands, validation expectations, and testing-scope
    boundaries
- `docs/development.md`
  - bootstrap, local environment setup, hooks, toolchain conventions, and daily
    development commands
- `docs/<topic>-plan.md` or `docs/plan.md`
  - transitional context, phased implementation, migration sequencing, or work
    grouping
- `docs/api.md`, `docs/runtime-model.md`, or other narrower reference docs
  - stable interfaces, runtime terminology, or narrower contracts when
    `docs/architecture.md` would otherwise become overloaded
- `docs/glossary.md`
  - recurring local terms when the project depends on stable term meanings or
    term ownership
- `docs/data-sources.md`
  - durable data artifacts the project consumes, produces, ships, or expects
    users or agents to work with

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
- `docs/runtime-model.md`
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

Private and default-profile projects can rely more safely on a user's global
bootstrap than public projects, but project-specific facts should still live in
the project.

- keep project-specific guidance visible inside the project's own agent surface
- keep root `AGENTS.md` short enough to act as a working brief, not a knowledge
  base
- use project-local source-of-truth docs for durable explanation, contracts, and
  local exceptions
- use local prompts or local review prompts for recurring project-specific work
  that should not become shared library behavior
- mention adopted shared recommendation docs explicitly rather than assuming they
  will be rediscovered indirectly

## Shared Private-Default Docs Review Model

This section is the narrative owner for the shared `private-default`
documentation-surface model used by validation.

### Private-Default Documentation Surface

For private or default-profile projects, the documentation surface is the set of
project-facing starting documents, durable source-of-truth docs, and local
supporting docs that humans and agents are expected to consult.

This surface normally includes:

- root `AGENTS.md`
- `README.md`
- folder-level `README.md` files when they materially orient a subtree
- `docs/architecture.md` or equivalent design docs
- `docs/testing.md` or equivalent verification docs
- `docs/development.md` when local setup or daily workflow needs a stable owner
- `docs/glossary.md` when recurring local terms need stable meaning
- narrower source-of-truth docs that are linked from starting documents or
  materially govern the requested scope

This surface does not automatically include:

- public package metadata
- docs-site output or generated built docs
- every file under `docs/` when the requested scope is narrower
- raw artifacts, logs, or generated files unless they are explicitly treated as
  source-of-truth docs by the project
- source code or tests except as evidence for document role, verification
  commands, or interface claims

### Scope Rule

The default review scope for private-default documentation should start from the
requested project or target root, then identify the project-facing documents that
materially define or govern that scope.

Start from these project-facing starting documents:

- root `AGENTS.md`
- `README.md`
- folder-level `README.md` files inside the requested scope
- `docs/architecture.md`
- `docs/testing.md`
- `docs/development.md` when present
- explicitly linked source-of-truth docs

Then review supporting docs only when they are linked from starting documents,
materially govern the requested scope, or are needed to judge source-of-truth
visibility and cross-document consistency.

Do not broaden the review into public-package documentation reachability unless
the project declares the `public-python` profile or the user explicitly asks for
public documentation review.

### Review Lens

Review the private-default documentation surface against these principles:

#### Working Entry And Orientation

- `AGENTS.md` should act as an operational working brief for agents.
- `README.md` should orient project participants and point to the most important
  next documents.
- Starting documents should expose the source-of-truth docs that materially
  shape normal work.

#### Source-Of-Truth Ownership

- Stable architecture, verification, development, runtime, data, and terminology
  facts should have clear owners.
- The same instruction should not drift across `AGENTS.md`, `README.md`, and
  `docs/` without a stronger owner.
- Transitional plans should not become the only owner for durable project facts.

#### Proportional Structure

- Small projects may keep minimal guidance in `README.md` and `AGENTS.md`.
- Larger projects should split durable guidance into named source-of-truth docs
  before the root working brief or README becomes overloaded.
- Missing docs are findings only when their absence materially weakens project
  work, validation, or source-of-truth visibility.

#### Validation And Development Visibility

- Verification expectations should be discoverable from `docs/testing.md` or an
  equivalent local source.
- Local setup, environment, and daily workflow expectations should be
  discoverable from `docs/development.md` or an equivalent source when the
  project depends on them.

#### Glossary And Terminology

- Recurring local terms should have a stable owner when ambiguity affects review,
  implementation, validation, or user-facing documentation.
- A project glossary is optional until recurring terminology becomes a material
  source of drift.

### Prompt-Design Consequences

The shared validation implementation that follows from this model should:

- evaluate the requested project or target root as a project-facing documentation
  system
- select relevant project-facing docs dynamically rather than assuming every
  `docs/` file is in scope
- keep `AGENTS.md` review focused on source-of-truth visibility and operational
  role when reached from documentation architecture review
- treat public-package metadata, generated docs, docstrings, examples, and docs
  site navigation as out of scope unless another profile or explicit user request
  brings them in
- use proportional findings so small projects are not forced into unnecessary
  document sprawl
- keep findings focused on document roles, linking, source-of-truth ownership,
  and cross-document consistency
