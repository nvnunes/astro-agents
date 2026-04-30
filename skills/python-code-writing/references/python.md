# Python Coding

## Purpose
Use this guide for editing, reviewing, refactoring, or writing Python code.

This guide is intended for codebases that value stable contracts, explicit ownership, readable structure, and minimal churn.

## Success Criteria
- Preserve behavior and contracts unless a change is explicitly intended.
- Improve readability, clarity, and consistency.
- Keep ownership of behavior, validation, and public interfaces clear.
- Prefer incremental changes over broad rewrites.
- Update tests, docs, or examples when behavior changes.

## Style
- Prefer explicit, readable, and symmetrical code.
- Prefer direct implementations over clever or overly generic ones.
- Keep one obvious owner for each responsibility.
- Keep public APIs clean and minimally surprising.
- Keep starting documents thin relative to the core Python API or logic.
- Keep the CLI as a thin wrapper over the Python API when a CLI exists.
- Favor incremental refactors that preserve contracts and make ownership or lifecycle clearer.

## Structure
- Prefer a consistent module order:
  - constants first
  - data structures next (`dataclass`, types, enums)
  - properties or simple accessors near the top
  - helper primitives next
  - composed helpers next
  - public starting documents last
- If a module follows a strong lifecycle, order methods by lifecycle instead.
- Separate logical blocks with clear section comments when file size,
  lifecycle shape, or ownership grouping would otherwise make scanning harder.
- Name section comments after real ownership or lifecycle groups rather than as
  decorative separators.
- Keep helpers in the narrowest module that owns the behavior.

## Project Architecture And Public API
- Keep a deliberate package-root public API boundary.
- Re-export only supported user-facing entrypoints from the package root.
- Keep public docs and examples aligned with supported package-root imports rather than internal module layout.
- Keep one obvious owner for each major concern.
- Keep one obvious owner for each contract, validation rule, and persisted field family.
- Keep lifecycle phases explicit when the project has a strong execution flow such as prepare, validate, load, bind, build, or execute.
- Keep compatibility layers, wrappers, and legacy shims outside the canonical package core when both must exist.
- Prefer typed request or config objects when a coherent input family benefits from an explicit type.
- Remove stale abstractions or weak indirection instead of preserving them for their own sake.

## Contracts And Validation
- Treat configs, schemas, stored data formats, and structured inputs as explicit contracts.
- Treat manifests, path rules, version rules, and persisted field families as explicit contracts when the project exposes them.
- Validate early with actionable errors.
- Avoid silent coercions and hidden fallback behavior.
- Keep one obvious owner per contract.
- Define stable keys, field names, and related constants in the narrowest shared module that owns them.
- Do not duplicate contract definitions across builders, validators, wrappers, and subclasses when one shared definition should exist.

## Abstraction And Naming
- Prefer explicit ownership over convenience abstractions.
- Remove stale indirection rather than preserving weak abstraction layers.
- Inline helpers that do too little to justify abstraction.
- Collapse duplicated validators, wrappers, or helpers when they enforce the same invariant.
- Prefer explicit names that match actual behavior.
- Do not use soft or misleading names such as `normalize` when a helper only validates or coerces.
- Prefer positional parameters for private methods unless keyword-only arguments clearly improve safety or readability.
- When normalizing values inside private methods, prefer rebinding the original variable name instead of introducing extra aliases unless both forms need to coexist.

## Comments And Docstrings
- Keep comments concise and technical.
- Avoid historical or conversational comments.
- Keep docstrings aligned with current behavior and ownership.
- Public modules, classes, functions, methods, and typed contract objects
  should document the contract they expose, not just the action they perform.
- Public functions and core validators should document the contract they
  enforce, not implementation history.
- Use a one-line public docstring only when the full caller contract is obvious
  from the name, signature, defaults, types, and surrounding context.
- Otherwise use a multi-line contract docstring.
- When contract details materially affect correct use, document the relevant
  role or ownership in the module or lifecycle, important inputs and return
  form, persisted paths, keys, or schema surfaces, relevant units, shapes, or
  cardinality, side effects or mutation expectations, important non-goals or
  exclusions, and error behavior.
- For public dataclasses and other typed config, request, result, or status
  objects, document field-level contract details individually when those fields
  materially affect correct use.
- Prefer a structured `Attributes:` block when it improves scanability,
  supports generated reference docs, or keeps peer public surfaces consistent.
- A prose-only summary is acceptable only when the object is small and each
  public field's contract is otherwise obvious.
- Document public hooks or extension points as real contracts.
- When underscore-prefixed hooks are part of a class contract, give them full
  docstrings covering purpose, inputs, return value, mutation expectations, and
  error behavior.
- Keep peer public APIs and typed contract objects in the same module at a
  comparable contract/reference level.
- In contract-heavy modules, document private helpers when they own schema
  shaping, persisted-path behavior, or other non-obvious invariants that are
  not obvious from code alone.
- When docstrings feed published reference docs, also apply
  `skills/project-docs-writing/references/project-docs.md`.

## Preservation And Revision
When revising existing Python code:
- Follow local codebase conventions over generic defaults in this guide.
- Do not change public APIs, stored data formats, externally visible behavior, or established data contracts unless explicitly intended.
- Do not rename fields, parameters, or helpers casually when existing names still match the behavior.
- Do not rewrite unrelated code for style alone.
- Make the critical path clearer, not richer.
- Prefer removing misleading abstraction over adding a new abstraction stage.

## Testing And Verification
- Add or adjust tests with every behavior change.
- Prefer tests of externally visible behavior over tests tightly coupled to internal structure.
- Preserve externally visible behavior unless a change is intentional and documented.
- Keep docs and examples aligned with code.
- Document canonical verification commands in the target project's local validation docs.
- Targeted tests are acceptable during iteration, but final verification should match the changed surface area.
- Finish substantial refactors with the full test or verification path that the project declares for the affected surface.
- If touched code contains stale comments, outdated docstrings, or duplicated contract checks, clean them up in the same change.

## Development Workflow
- Keep bootstrap and environment setup project-local rather than relying on undocumented ambient tooling.
- Prefer a project-local environment for Python commands, tests, docs builds, and CLI runs when feasible.
- When a project uses Conda, prefer a project-local environment such as `./.conda` rather than a user-global environment.
- Keep canonical verification commands in the target project's local testing or validation source.
- Keep bootstrap, environment, hooks, and daily commands in the target project's local development docs when those workflows are recurring.
- When published reference pages are generated from docstrings, treat docstring changes for that reference surface as docs-affecting changes in the normal verification path.

## Adaptation
- Editing existing code: follow established local patterns unless they are clearly harmful or the user asks for a deliberate style change.
- Writing new modules: use the default module organization in this guide unless the codebase already uses a different stable pattern.
- Reviewing code: prefer concrete findings over speculative redesigns.
- Reviewing public code: flag action-only docstrings where contract details are
  needed.
- Reviewing public code: flag missing lifecycle or ownership context in public
  module or class docstrings.
- Reviewing public code: flag inconsistent docstring completeness or structure
  across peer public APIs or typed contract objects.
- Reviewing large, lifecycle-heavy, or contract-heavy modules: flag missing
  structural section comments when they would materially improve scanability or
  ownership clarity.
- Reviewing contract-heavy modules: flag undocumented private helpers that own
  schema shaping, persisted-path behavior, or other non-obvious contract logic.
- Refactoring code: prioritize clarity, ownership, lifecycle order, and contract preservation over stylistic novelty.

## Output
- When editing code, make the change directly and keep explanation brief unless more detail is requested.
- When reviewing code, identify concrete issues first.
- When both review and revision are requested, give the review first and then provide the revised code or patch plan.
