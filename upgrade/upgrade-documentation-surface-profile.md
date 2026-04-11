# Upgrade Documentation Surface Profile

## Purpose
Use this prompt to declare or update the target repo's documentation surface profile in the root `AGENTS.md` before planning, editing, review, or progress work.

Use it when the user wants to:

- start an upgrade by recording the documentation surface profile first
- change an already declared documentation surface profile
- repair a missing or stale documentation surface profile declaration in the root `AGENTS.md`

## Inputs

- target root or target paths
- documentation surface profile provided explicitly by the user
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

If the user has not explicitly provided the documentation surface profile, stop and ask for it before editing the root `AGENTS.md`.

## Common Workflow

1. Read `astro-agents/docs/usage.md` and `astro-agents/docs/upgrade-design.md` first.
2. Inspect the target repo's root `AGENTS.md` when it exists.
3. If the root `AGENTS.md` already exists:
   - preserve the repo's existing routing and source-of-truth instructions unless they directly conflict with the user's explicit profile choice
   - add or update a short `## Scope` section near the top when needed
   - record the profile using this exact line: `- Documentation surface profile: <profile>.`
4. If the root `AGENTS.md` does not exist:
   - create a minimal bootstrap root `AGENTS.md` using the repo template shape from `astro-agents/docs/usage.md`
   - keep it limited to prompt routing, a short `## Scope` section with the profile declaration, and a short source-of-truth section
5. Keep this setup prompt narrow. Its job is to make the profile visible and reliable for later upgrade prompts, not to complete the broader `minimum repo-level AGENTS.md` task.

## Exclusions

- do not infer, choose, or reinterpret the documentation surface profile
- do not rewrite unrelated repo docs
- do not perform the broader `minimum repo-level AGENTS.md` normalization task here
- do not create a `docs/upgrade/*.md` artifact for this setup step

## Output

Write or update the target repo's root `AGENTS.md`.

When creating a new bootstrap root `AGENTS.md`, use this minimum structure:

```md
# <Repo> Agent Brief

## Prompt Routing
- Follow any higher-level workspace prompt-routing instructions when present.
- When higher-level routing selects a higher-level prompt subtree, check the corresponding subtree under `agents/` for matching local prompts.
- Keep applicable higher-level and matching local prompts active together.
- When applicable instructions conflict, use the applicable precedence rules to decide which instruction governs.
- Use other prompts under `agents/` when they directly match the request and do not correspond to a higher-level counterpart.

## Scope
- Documentation surface profile: `<profile>`.

## Source Of Truth
- Use `README.md` for the repo overview and major entrypoints.
- Use `docs/architecture.md` for structure, ownership, and interfaces when present.
- Use `docs/testing.md` for validation requirements and canonical checks when present.
- Use any other named local source-of-truth docs directly.
```

Return a short setup summary with:

- root `AGENTS.md` path
- declared documentation surface profile
- whether the root `AGENTS.md` was created or updated
- any follow-up note about later normalization by `minimum repo-level AGENTS.md`
