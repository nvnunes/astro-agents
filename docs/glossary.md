# Glossary

This document is the human-facing source of truth for shared terminology in `astro-agents`.

Use it when a design term, review term, or prompt-system term needs a stable meaning that both humans and agents can reuse consistently.

## Terms

### Agent Surface

The set of agent-facing files and structures that shape how an agent navigates, interprets, or applies a repo.

Typical examples:

- `AGENTS.md`
- prompt assets
- agent-facing routing files
- repo-local prompts under `agents/`

Use this term when the concern is the operational interface presented to the agent rather than the underlying implementation.

### Prompt Surface

The subset of the agent surface made of prompt assets and prompt-routing files.

Use this term when the discussion is specifically about prompts, prompt structure, or prompt behavior rather than all agent-facing materials.

### Surface

A stable interaction layer that a reader, agent, or tool works against.

Examples:

- a repo's agent surface
- a repo's documentation surface
- a validation surface

Use this term when the concern is the usable interface exposed by a set of files rather than the internal history or implementation details behind them.

### Router

A file whose primary job is to direct the agent to the right narrower prompt area, guide, or source of truth.

Routers should route and scope. They should not restate the full substantive behavior of the deeper asset they activate.

Examples:

- top-level `AGENTS.md`
- subgroup `AGENTS.md`

### Prompt Asset

A substantive reusable prompt document that carries operational behavior rather than just routing or explanation.

Examples:

- writing guides
- coding guides
- validation prompts

Use this term to distinguish the prompt itself from the router that selects it and the README that explains it.

### Source Of Truth

The document that should be treated as authoritative for a particular kind of information within a given scope.

Examples:

- `docs/architecture.md` for hierarchy design
- `docs/testing.md` for validation workflow
- a repo `AGENTS.md` for repo-local operational constraints

Use this term when clarifying ownership and preventing duplicated or drifting guidance.

### Local Activation

A repo-level or subtree-level instruction that explicitly tells the agent to use a specific shared prompt asset in that local context.

Use this term when a local `AGENTS.md` points downward to a shared guide instead of restating that guide.

### Scope Drift

The failure mode where a file, prompt, or document expands beyond its intended role and starts doing a neighboring file's job.

Examples:

- an `AGENTS.md` file becoming a design doc
- a validation prompt becoming a generic rewrite prompt
- a repo doc becoming both overview and testing source of truth

Use this term when the problem is role expansion rather than simple verbosity.

### Smell

A sign that the current structure or wording may be misaligned even if there is not yet a concrete defect.

A smell is not automatically a bug. It is a signal that category boundaries, naming, ownership, or layering may need closer inspection.

Use this term when pointing out structural unease that deserves investigation before it hardens into drift or duplication.

### Public-Safe

Safe to keep in a repo that may later become public because it does not depend on private workspace paths, private repo coupling, or hidden local assumptions.

Use this term when evaluating whether examples, templates, or repo files remain portable outside the private workspace.

### Subgroup

A narrower prompt family inside the shared library, such as `authoring/` or `validation/`.

Use this term when discussing a prompt area that has its own router, README, and prompt assets.

### Review Lens

A named criterion or angle of evaluation used by a validation prompt.

Examples:

- source-of-truth boundaries
- hierarchy behavior
- prompt role clarity

Use this term when the review needs explicit dimensions of assessment rather than a generic critique.

## Notes

- Add a term here when it is used repeatedly across prompts or docs and needs one stable meaning.
- Prefer linking here over redefining the same term in multiple docs unless a narrower local override is necessary.
