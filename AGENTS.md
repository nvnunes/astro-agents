# AGENTS.md

This file is the root working brief for the `astro-agents` project itself.

## Scope
- This file applies to work inside the `astro-agents` project.
- It provides project-local context, source-of-truth pointers, and validation expectations.
- `skills/` is the canonical runtime capability surface for reusable `astro-agents` behavior.

## Source Of Truth
- Use `README.md` for the project overview and major starting documents.
- Use `docs/architecture.md` for library structure, scope ownership, validation model, and maintenance expectations in this project.
- Use `docs/usage.md` for downstream adoption, project setup, project-check setup, and starter prompts.
- Use `docs/runtime-model.md` for runtime terminology, control-flow concepts, and terminology-reframing guidance in this project.
- Use `docs/testing.md` for project checks and commands.
- Use `docs/research-log-mechanical-validator-spec.md` for the normative implementation contract for the code-only research-log mechanical validator, its inputs, generated state, cache, diagnostics, and tests.
- Use `docs/research-log-reproduction-spec.md` for the normative execution-state, scheduling, migration, and mechanical-reproduction contract.
- Use any other named local source-of-truth docs directly.

## Validation
- Use `docs/testing.md` to select the project checks that match the change.
