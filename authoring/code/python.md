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
- Keep entrypoints thin relative to the core Python API or logic.

## Structure
- Prefer a consistent module order:
  - constants first
  - data structures next (`dataclass`, types, enums)
  - properties or simple accessors near the top
  - helper primitives next
  - composed helpers next
  - public entrypoints last
- If a module follows a strong lifecycle, order methods by lifecycle instead.
- Separate logical blocks with clear section comments.
- Keep helpers in the narrowest module that owns the behavior.

## Contracts And Validation
- Treat configs, schemas, stored data formats, and structured inputs as explicit contracts.
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
- Public functions and core validators should document the contract they enforce, not implementation history.
- Underscore-prefixed hooks or extension points that are part of a class contract should have full docstrings covering purpose, inputs, return value, mutation expectations, and error behavior.

## Preservation And Revision
When revising existing Python code:
- Follow local codebase conventions over generic defaults in this guide.
- Do not change public APIs, stored data formats, externally visible behavior, or established data contracts unless explicitly intended.
- Do not rename fields, parameters, or helpers casually when existing names still match the behavior.
- Do not rewrite unrelated code for style alone.
- Make the critical path clearer, not richer.
- Prefer removing misleading abstraction over adding a new abstraction layer.

## Testing And Verification
- Add or adjust tests with every behavior change.
- Prefer tests of externally visible behavior over tests tightly coupled to internal structure.
- Preserve externally visible behavior unless a change is intentional and documented.
- Keep docs and examples aligned with code.
- If touched code contains stale comments, outdated docstrings, or duplicated contract checks, clean them up in the same change.

## Adaptation
- Editing existing code: follow established local patterns unless they are clearly harmful or the user asks for a deliberate style change.
- Writing new modules: use the default module organization in this guide unless the codebase already uses a different stable pattern.
- Reviewing code: prefer concrete findings over speculative redesigns.
- Refactoring code: prioritize clarity, ownership, lifecycle order, and contract preservation over stylistic novelty.

## Output
- When editing code, make the change directly and keep explanation brief unless more detail is requested.
- When reviewing code, identify concrete issues first.
- When both review and revision are requested, give the review first and then provide the revised code or patch plan.
