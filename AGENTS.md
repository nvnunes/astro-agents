# AGENTS.md

This repo is the shared prompt library itself, so this root `AGENTS.md` acts as the library entry router rather than as a downstream repo brief.

## Prompt Routing
- Route authoring work, including prompt, documentation, and `AGENTS.md` writing or revision work, plus source-code writing, revision, or review work, into `authoring/AGENTS.md`.
- Route general planning work, including ad hoc plans, execution plans, next-step planning, sequencing, roadmaps, and review plans that are not agent surface upgrade-planning, into `authoring/AGENTS.md`.
- Route review or validation of prompts, documentation, `AGENTS.md`, routing, or hierarchy into `validation/AGENTS.md`.
- When routing in this file selects a shared prompt subtree, check the corresponding subtree under `agents/` for matching local prompts.
- Keep applicable shared and matching local prompts active together.
- When applicable instructions conflict, use the applicable precedence rules to decide which instruction governs.
- Use other prompts under `agents/` when they directly match the request and do not correspond to a shared counterpart in this repo.

## Precedence
- More specific subtree-level `AGENTS.md` files take precedence within their scope.
- Otherwise instructions in this file apply by default within this repository.
- When matching local prompts under `agents/` and shared prompts in this repo both apply, keep compatible guidance from both.
- When their instructions conflict, the higher-precedence instruction governs.

## Source Of Truth
- Use `README.md` for the repo overview and major entrypoints.
- Use `docs/architecture.md` for hierarchy design, layer ownership, precedence, validation model, and maintenance expectations in this repo.
- Use `docs/testing.md` for validation requirements and canonical review checks in this repo.
- Use any other named local source-of-truth docs directly.

## Validation
- When a task changes agent surface files in this repo, consult `docs/testing.md` and run the required validation before treating the work as complete.
