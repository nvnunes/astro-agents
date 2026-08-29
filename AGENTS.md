# AGENTS.md

This file is the root working brief for the `astro-agents` project itself.

## Scope
- This file applies to work inside the `astro-agents` project.
- It provides project-local context, source-of-truth pointers, and validation expectations.
- `skills/` is the canonical runtime capability surface for reusable `astro-agents` behavior.

## Source Of Truth
- Use `README.md` for the project overview and major starting documents.
- Use `docs/architecture.md` for library structure, scope ownership, validation model, and maintenance expectations in this project.
- Use `docs/usage.md` for downstream adoption, project setup, shared validation usage, and starter prompts.
- Use `docs/runtime-model.md` for runtime terminology, control-flow concepts, and terminology-reframing guidance in this project.
- Use `docs/testing.md` for validation requirements and canonical review checks in this project.
- Use `docs/research-log-evidence-record-spec.md` for the normative research-log evidence, mechanical-validation, generated-state, and upgrade contract.
- Use any other named local source-of-truth docs directly.

## Validation
- When a task changes agent surface files in this project, consult `docs/testing.md` and run the required validation before treating the work as complete.

## Temporary Files
- Use the project-local `tmp/` directory for temporary files worth backing up or retaining after the current task.
- Use `/private/tmp` for temporary files needed only during the current task and safe to delete when it ends.
