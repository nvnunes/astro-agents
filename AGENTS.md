# AGENTS.md

This repo is the shared prompt library itself, so this root `AGENTS.md` acts as the library entry dispatcher rather than as a downstream repo brief.

## Prompt Routing And Workflow
- Route authoring work, including prompt, documentation, and `AGENTS.md` writing or revision work, plus source-code writing, revision, or review work, into `authoring/AGENTS.md`.
- Route general planning work, including ad hoc plans, execution plans, next-step planning, sequencing, roadmaps, and review plans that are not validation or repo-upgrade work, into `authoring/AGENTS.md`.
- Route review or validation of prompts, documentation, `AGENTS.md`, prompt routing and workflow, or route structure into `validation/AGENTS.md`.
- Route requests to upgrade a repo, review a repo for upgrade readiness, plan or propose how to group the upgrade work from current repo state, or assess a repo against the shared upgrade design into `validation/AGENTS.md`.
- Route requests to design, revise, or document the shared upgrade model, including `docs/upgrade-design.md`, into `authoring/AGENTS.md`.
- When this file routes work into a shared prompt subtree, check the corresponding subtree under `agents/` for matching local prompts.
- Keep applicable shared and matching local prompts active together.
- When applicable instructions conflict, use the applicable instruction-authority rules to decide which instruction applies.
- Use other prompts under `agents/` when they directly match the request and do not correspond to a shared counterpart in this repo.

## Instruction Authority And Conflict Handling
- More specific subtree-level `AGENTS.md` files have higher instruction authority within their scope.
- Otherwise instructions in this file apply by default within this repository.
- When matching local prompts under `agents/` and shared prompts in this repo both apply, keep compatible guidance from both.
- When their instructions conflict, follow the higher-authority instruction.

## Source Of Truth
- Use `README.md` for the repo overview and major starting documents.
- Use `docs/architecture.md` for route structure, scope ownership, instruction authority, validation model, and maintenance expectations in this repo.
- Use `docs/runtime-model.md` for runtime terminology, control-flow concepts, and terminology-reframing guidance in this repo.
- Use `docs/testing.md` for validation requirements and canonical review checks in this repo.
- Use `docs/upgrade-design.md` for the upgrade process design and next steps.
- Use any other named local source-of-truth docs directly.
- Treat deeper source-of-truth docs as supporting `Context` by default. Treat them as active `Instructions` only when higher-authority instructions explicitly delegate narrower authority to them.

## Validation
- When a task changes agent surface files in this repo, consult `docs/testing.md` and run the required validation before treating the work as complete.
