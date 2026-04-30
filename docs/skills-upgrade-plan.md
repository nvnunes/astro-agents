# Skills Upgrade Plan

This document records the completed migration of `astro-agents` from a routed prompt-family library into a skills-first shared library.

The migration was intentionally breaking. Old `authoring/`, `validation/`, `research-log/`, `agents/validation/`, and `upgrade/` prompt paths were not retained as compatibility redirects.

## Final Shape

- `skills/`: user-facing runtime capabilities packaged as `SKILL.md` plus `references/`, optional `scripts/`, optional `assets/`, and required `agents/openai.yaml` metadata.
- `examples/`: example downstream project documents.
- `docs/`: source-of-truth docs for `astro-agents` itself.

Runtime skill discovery is the primary activation path for reusable `astro-agents` behavior. Root `AGENTS.md` files now provide project-local working context, source-of-truth pointers, and validation expectations rather than acting as task routers.

## Completed Phases

### Phase 1: Convert To Skills

Status: completed.

Delivered user-facing skill packages:

- `prompt-writing`
- `agents-md-writing`
- `skill-md-writing`
- `technical-writing`
- `science-writing`
- `project-docs-writing`
- `readme-writing`
- `concept-writing`
- `plan-writing`
- `python-code-writing`
- `research-logging`
- `documentation-surface-review`
- `agent-surface-review`
- `code-quality-review`
- `project-upgrade-planning`

The old prompt-family directories were removed after their useful content was moved into skill packages. Research-log creation and writing were merged into `research-logging`. Python writing and review behavior were split between `python-code-writing` and `code-quality-review`.

### Phase 2: Update Astro-Agents Docs

Status: completed.

The project documentation now describes the skills-first structure:

- `README.md` gives the project overview.
- `docs/architecture.md` owns structure, scope, and maintenance expectations.
- `docs/usage.md` owns downstream adoption guidance.
- `docs/testing.md` owns validation expectations.
- `docs/runtime-model.md`, `docs/glossary.md`, and `docs/future/` preserve terminology and future runtime design context.
- `skills/project-upgrade-planning/references/upgrade-model.md` owns the shared upgrade model.

Standalone downstream guidance was replaced with `docs/usage.md` guidance, `examples/`, and skill-local references.

### Phase 3: Add Skill Metadata And Runtime Checks

Status: completed.

Resolved decisions:

- Keep canonical skill sources in root `skills/`.
- Use a user-level nested symlink for local Codex discovery: `$HOME/.agents/skills/astro-agents -> <astro-agents-path>/skills`.
- Treat runtime skill discovery, not root `AGENTS.md`, as the primary skill activation mechanism.
- Require `agents/openai.yaml` for every user-facing `astro-agents` skill.
- Keep `agents/openai.yaml` metadata minimal and aligned with `SKILL.md`.
- Use `scripts/validate_agent_surface.py` as the deterministic static validation harness.
- Use `tests/activation_cases.csv` as the maintained activation eval fixture.
- Use `python3 scripts/validate_agent_surface.py --codex-discovery` for the local Codex discovery smoke test.
- Use `python3 scripts/validate_agent_surface.py --activation-eval` only when activation behavior may have changed.

The root `AGENTS.md` was reduced to project-local scope, source-of-truth, and validation guidance. Project-local review prompts whose only purpose was legacy routing validation were removed.

### Phase 4: Update Peer Projects

Status: completed.

Updated peer surfaces:

- workspace-level `AGENTS.md`
- `ao-predict`
- `ao-sky`
- `cubesim`
- `girmos-aosims`
- `pubify-data`
- `pubify-mpl`
- `pubify-ppt`
- `pubify-pubs`
- `pubify-tex`
- `research-workflow`

The peer updates removed retired `astro-agents` path references, replaced prompt-family references with `$skill-name` usage, preserved project-local facts and commands, updated `pubify` scaffold `AGENTS.example.md` files, and deleted the obsolete sibling `upgrade/` rollout folder.

## Validation Used

- `python3 scripts/validate_agent_surface.py`
- `python3 scripts/validate_agent_surface.py --codex-discovery` where discovery behavior changed
- `git diff --check`
- stale-reference scans for retired prompt-family paths
- targeted peer-repo checks and tests where scaffold templates or generated docs changed

## Completion Notes

This plan is now an execution record, not an active source of truth.

Durable facts that must survive this document are now owned by:

- `docs/architecture.md` for project structure and metadata expectations
- `docs/usage.md` for downstream adoption and Codex skill discovery setup
- `docs/testing.md` for validation commands and activation eval guidance
- `skills/project-upgrade-planning/references/upgrade-model.md` for the shared upgrade model
