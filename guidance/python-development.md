# Python Development

This document is a shared recommendation for downstream Python repos that use
`astro-agents`.

Use it when defining repo-level engineering guidance across architecture,
coding policy, development workflow, and review priorities.

Use `docs/usage.md` for how to include this guidance in a downstream repo. Use
`authoring/code/python.md` for task-time Python editing, review, refactoring,
and revision behavior. When Python-development changes require updates to
repo-local source-of-truth docs such as `docs/architecture.md`,
`docs/testing.md`, or `docs/development.md`, use
`authoring/writing/repo-docs.md` for those repo-doc revisions.

Keep commands, package boundaries, persisted contracts, lifecycle rules, and
exceptions in repo-local source-of-truth docs.

## Architecture Guidance

- Keep a deliberate package-root public API boundary.
- Re-export only supported user-facing entrypoints from the package root.
- Keep public docs and examples aligned with supported package-root imports
  rather than internal module layout.
- Keep the CLI as a thin wrapper over the Python API.
- Keep one obvious owner for each major concern.
- Keep one obvious owner for each contract, validation rule, and persisted
  field family.
- Treat configs, schemas, stored data formats, manifests, and path or version
  rules as explicit contracts.
- Validate contracts early with actionable errors.
- Avoid silent coercions, hidden fallback behavior, or split ownership of the
  same invariant across multiple layers.
- Keep lifecycle phases explicit when the repo has a strong execution flow such
  as prepare, validate, load, bind, build, or execute.
- Keep compatibility layers, wrappers, and legacy shims outside the canonical
  package core when both must exist.
- Prefer typed request or config objects when a coherent input family benefits
  from an explicit type.
- Favor incremental refactors that preserve contracts and make ownership or
  lifecycle clearer.
- Remove stale abstractions or weak indirection instead of preserving them for
  their own sake.

## Coding Policy

For task-time code editing or review behavior, pair this guidance with
`authoring/code/python.md`.

- Prefer explicit, readable, and symmetrical code with clear ownership and
  minimally surprising public APIs.
- Keep helpers in the narrowest module that owns the behavior.
- Prefer direct implementations over convenience abstractions that blur
  ownership.
- Keep module or class organization consistent with lifecycle when one exists.
- Keep comments and docstrings aligned with current behavior and ownership.
- Use `authoring/code/python.md` for detailed public-API and hook docstring
  expectations, and `authoring/writing/repo-docs.md` when docstrings define
  published docs.
- Add or adjust tests, docs, or examples whenever behavior or supported usage
  changes.
- Do not rewrite unrelated code for style alone.

## Development Workflow

- Keep bootstrap and environment setup project-local rather than relying on
  undocumented ambient tooling.
- Prefer a project-local environment for Python commands, tests, docs builds,
  and CLI runs when feasible.
- When a repo uses Conda, prefer a repo-local environment such as `./.conda`
  rather than a user-global environment.
- When that environment choice should affect first-turn behavior, a minimal
  `Working Rules` bullet is:

```md
- Use the local `./.conda` environment and the workflow in `docs/development.md` for Python commands, test runs, and docs builds unless a task explicitly requires something else.
```

- Document canonical verification commands in `docs/testing.md`.
- Document bootstrap, environment, hooks, and daily commands in
  `docs/development.md`.
- Mirror stable verification steps in a versioned bootstrap path or repo-owned
  git hooks when that improves consistency.
- When the repo publishes docs, keep a strict docs build in the normal
  verification path.
- When published reference pages are generated from docstrings, treat
  docstring changes for that reference surface as docs-affecting changes in the
  normal verification path.
- Targeted tests are acceptable during iteration, but final verification should
  match the changed surface area.
- Finish substantial refactors with the full test or verification path that the
  repo declares for the affected surface.

## Review Priorities

When reviewing or refactoring downstream Python repos, favor:

- contract ownership clarity
- lifecycle clarity
- stable public API boundaries
- removal of stale abstractions over preserving weak indirection
- incremental refactors over broad rewrites
