# AGENTS.md

This project is the shared prompt library itself, so this root `AGENTS.md` acts as the library entry dispatcher rather than as a downstream project brief.

## Prompt Routing And Workflow
- Route research-log-specific requests to design, revise, or apply reusable research-log guidance, theme-document hierarchies, source-plus-summary research logs, or upgrades from source documents into theme records to `research-log/AGENTS.md`.
- Route authoring work, including prompt, documentation, and `AGENTS.md` writing or revision work, plus source-code writing, editing, refactoring, or code-authoring-guide selection, into `authoring/AGENTS.md`.
- Route general planning work, including ad hoc plans, execution plans, next-step planning, sequencing, roadmaps, and review plans that are not validation or project-upgrade work, into `authoring/AGENTS.md`.
- Route review or validation of prompts, documentation, `AGENTS.md`, prompt routing and workflow, or route structure into `validation/AGENTS.md`.
- Route current-state code-quality or source-code-quality review into `validation/AGENTS.md`.
- Route requests to upgrade a project, review a project for upgrade readiness, plan or propose how to group the upgrade work from current project state, or assess a project against the shared upgrade design into `validation/AGENTS.md`.
- Route requests to design, revise, or document the shared upgrade model, including `docs/upgrade-design.md`, into `authoring/AGENTS.md`.

## Scope
- This file provides the root routing guidance for this project.
- More specific subtree-level `AGENTS.md` files provide narrower local guidance within their scope.

## Source Of Truth
- Use `README.md` for the project overview and major starting documents.
- Use `docs/architecture.md` for route structure, scope ownership, validation model, and maintenance expectations in this project.
- Use `docs/usage.md` for downstream adoption, shared guidance inclusion, shared validation usage, and starter-request patterns.
- Use `docs/runtime-model.md` for runtime terminology, control-flow concepts, and terminology-reframing guidance in this project.
- Use `docs/testing.md` for validation requirements and canonical review checks in this project.
- Use `docs/upgrade-design.md` for the upgrade process design and next steps.
- Use any other named local source-of-truth docs directly.

## Validation
- When a task changes agent surface files in this project, consult `docs/testing.md` and run the required validation before treating the work as complete.
- When the root `AGENTS.md` is changed, also run `agents/validation/root-agents-consistency-review.md`.
