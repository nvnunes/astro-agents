# Usage

This document is the human-facing source of truth for how repos in this workspace should use the shared prompt library and structure their supporting documents.

Use `docs/glossary.md` when usage guidance depends on shared prompt-system terms that need one stable meaning across repos.

## Quick Navigation

Use this document in two passes:

- read the early sections for decision rules about minimum support, supporting documents, document splitting, and cross-linking
- use the later sections when you need concrete templates, examples, or suggested patterns to adapt

Key sections:

- `Minimum Agentic Support Base`
  - baseline document set for nontrivial repos
- `Recommended Supporting Documents`
  - when to add `docs/` and repo-local `agents/`
- `Document Naming And Cross-Linking`
  - stable naming and source-of-truth surfacing rules
- `Repo AGENTS Template`
  - recommended root `AGENTS.md` shape
- `Recommended docs/testing.md Pattern`
  - validation contract structure and trigger design
- `Workspace Bootstrap Example`
  - top-level bootstrap pattern for a shared workspace

## Minimum Agentic Support Base

Every nontrivial repo should provide a small minimum document base that helps both humans and agents work effectively.

Recommended minimum:

- `AGENTS.md`
  - the operational working brief for agents
- `README.md`
  - the human-facing overview, setup entrypoint, and orientation document
- `docs/architecture.md` or an equivalent design document
  - the source of truth for system structure, boundaries, and ownership
- `docs/testing.md` or an equivalent verification document
  - the source of truth for canonical test commands and verification expectations

If a repo is still too small to justify separate documents, keep the minimum necessary guidance in `README.md` and `AGENTS.md`. Split it out once the content becomes reusable, stable, or operationally important.

## Recommended Supporting Documents

For repos that are large enough to justify them, prefer a small stable set of supporting documents over one overloaded `AGENTS.md`.

Recommended documents:

- `README.md`
  - repo purpose, setup, entrypoints, and orientation
- `AGENTS.md`
  - operational instructions for agents working in the repo
- `docs/architecture.md`
  - boundaries, responsibilities, ownership rules, and extension points
- `docs/testing.md`
  - canonical commands, test layers, and required verification bar
- `agents/` when needed
  - a repo-local agent-prompt library for prompts that are too local to belong in the shared library
- `docs/` plans or design notes as needed
  - long-lived rationale, migration plans, refactor plans, or decision records

These names are recommendations, not a rigid schema. If a repo already uses stable alternative names, preserve them.

## Suggested Sections By Document Type

Use regular section structure where it improves predictability.

Suggested sections for repo `AGENTS.md`:

- `Prompt Routing`
- `Scope`
- `Architecture`
- `Contracts`
- `Workflow`
- `Testing Expectations`
- `Review Lens`

Suggested sections for `docs/architecture.md` or equivalent:

- `Purpose`
- `System Boundaries`
- `Component Or Module Map`
- `Ownership Rules`
- `Public API Or Entry Points`
- `Contracts And Data Flow`
- `Extension Points` or `Variation Points`
- `Known Constraints`

Suggested sections for `docs/testing.md` or equivalent:

- `Purpose`
- `Canonical Commands`
- `Agent-Surface Validation`
- `Test Layers` or `Test Categories`
- `Required Verification Bar`
- `Environment Or Fixture Assumptions`
- `Regression Priorities`

Suggested sections for long-lived plan or design documents:

- `Goal`
- `Scope`
- `Non-Goals`
- `Phases`, `Passes`, or `Workstreams`
- `Risks` or `Open Questions`
- `Verification`
- `Deferred Work`

These are defaults, not mandates. Use them when they improve consistency and make the document easier to scan.

## When To Split Guidance Into A Separate Document

Create or expand a separate supporting document when:

- the guidance is too long for `AGENTS.md`
- the guidance is explanatory rather than operational
- the information needs to stay stable across many tasks
- the same instruction would otherwise be repeated across multiple files
- the repo has enough complexity that agents need a persistent source of truth

Keep guidance in `AGENTS.md` when:

- the rule is short and operational
- the instruction is primarily about how to work safely right now
- the repo is simple enough that further document split would add overhead without clarity

The public guidance behind this recommendation is summarized in the `References` section below. [1][2][3][4]

## Document Naming And Cross-Linking

Prefer stable, predictable document names when they fit the repo:

- `README.md`
- `AGENTS.md`
- `docs/architecture.md`
- `docs/testing.md`
- `docs/<topic>-plan.md`
- `docs/<topic>-design.md`

These names are not required, but predictable names make documents easier for both humans and agents to find.

When these documents exist:

- surface important source-of-truth docs from `AGENTS.md` or `README.md`
- avoid orphan documents that are never linked from the working brief or repo overview
- make it clear which document is operational guidance and which is deeper explanation
- avoid scattering the same instruction across multiple files without a clear owner

## Examples

Use examples selectively. [2]

Add examples when they resolve repeated ambiguity, clarify a routing boundary, or prevent a known failure mode.

Avoid adding examples when they mainly repeat guidance that is already clear without them.

## Repo AGENTS Template

Public guidance on `AGENTS.md` is consistent on a few points:

- keep it operational rather than encyclopedic [2][3]
- cover what an agent needs to work safely and effectively [2]
- use nested `AGENTS.md` files for narrower scopes [1][2][4]
- let the nearest applicable file win [1][2][4]

For this workspace, repo-level `AGENTS.md` files should follow that model and stay focused on repo-local routing and source-of-truth activation.

Recommended default template:

```md
# <Repo> Agent Brief

## Prompt Routing
- Follow any higher-level workspace prompt-routing instructions when present.
- When higher-level routing selects a higher-level prompt subtree, check the corresponding subtree under `agents/` first.
- Use other prompts under `agents/` when they directly match the request and do not correspond to a higher-level counterpart.

## Precedence
- More specific subtree-level `AGENTS.md` files take precedence within their scope.
- Otherwise instructions in this file apply by default within this repository.
- Within this scope, use matching local prompt assets under `agents/` before falling back to higher-level prompt assets.

## Source Of Truth
- Use `README.md` for the repo overview and major entrypoints.
- Use `docs/architecture.md` for structure, ownership, and interfaces when present.
- Use `docs/testing.md` for validation requirements and canonical checks when present.
- Use any other named local source-of-truth docs directly.
```

This is the default recommendation. It is enough to establish local routing, activate the intended shared or local guides, and surface the source-of-truth docs that define the repo's agent surface.

Add sections like these only when inline local guidance materially improves runtime use:

```md
## Scope
- This repo owns `<repo role>`.
- Prefer `<main change discipline>` for changes in this repo.

## Architecture
- Use `docs/architecture.md` for boundaries, ownership, and interfaces when present.
- Treat this section as a short local summary, not a replacement for `docs/architecture.md`.
- Treat `<public API boundary>` as the intended public interface.
- Keep `<behavior or asset>` in `<layer, module, or path>`.

## Contracts
- Preserve `<important contract>`.
- Preserve `<validation or compatibility expectation>`.

## Workflow
- Use `<repo-specific command or environment rule>`.
- Respect `<deployment or operational constraint>`.

## Testing Expectations
- Use `docs/testing.md` for validation requirements and canonical checks when present.
- Treat this section as a short local summary, not a replacement for `docs/testing.md`.
- Run `<canonical repo-specific command>` for meaningful changes.

## Review Lens
- Prioritize `<repo-specific review concern>`.
- Watch for `<repo-specific risk>`.
```

For public-safe repo files, prefer this kind of generic activation language over hardcoded workspace paths. The repo file can name the kind of shared guide that should govern locally without assuming a specific private prompt-library location.

When deeper source-of-truth docs exist, repo `AGENTS.md` should surface them explicitly instead of assuming the agent will discover them on its own.

## Workspace Bootstrap AGENTS Template

At the workspace level, for example in `/Projects`, prefer a much thinner bootstrap file:

```md
# Workspace Agent Brief

## Prompt Routing
- When available, use `agents/AGENTS.md` before falling back to `astro-agents/AGENTS.md`.
```

This workspace-level file is intentionally thinner than a repo `AGENTS.md`. Its job is to bootstrap the shared router and hold workspace-only preferences, not to compete with the prompt library or repo-local files.

## User-Local Override AGENTS Template

When a workspace needs user-local reusable overrides that should apply across multiple repos without belonging in the shared library, prefer a separate `Projects/agents/AGENTS.md` layer:

```md
# User Agent Overrides

## Prompt Routing
- When higher-level routing selects a subtree under `astro-agents/`, check the corresponding subtree here first.
- Use other prompts here when they directly match the request and do not correspond to a shared counterpart under `astro-agents/`.

## Precedence
- Use matching local prompt assets here when present; otherwise fall back to the matching path under `astro-agents/`.
```

This user-local layer does not need a `README.md` by default. Add one only if the local override library becomes large enough that humans need deeper explanatory guidance.

## Recommended `docs/testing.md` Pattern

When a repo uses shared validation from this prompt library, `docs/testing.md` should define agent-surface validation explicitly.

Recommended pattern:

```md
## Agent-Surface Validation

Use agent-surface validation when changes affect the repo's agent-facing surfaces, including `AGENTS.md`, `README.md`, relevant files under `docs/`, or other prompt and instruction assets.

### Required Reviews

- Changes to `AGENTS.md` files:
  - run agent-surface writing review
  - run hierarchy-behavior review

- Changes to `README.md` or files under `docs/`:
  - run agent-surface writing review
  - run documentation-architecture review

- Changes that substantially alter prompt or instruction structure:
  - run a full agent-surface review

### Shared Validation

When higher-level shared validation prompts are available, use them as the default review mechanism for these checks.

### Local Validation

When the repo provides local validation prompts under `agents/validation/`, use them for repo-specific checks that do not belong in the shared validation library.

### Repo-Specific Validation

When a repo needs agent-facing prompts that are specific to its own structure, examples, or exceptions, keep them in a local `agents/` library and surface them from `docs/testing.md` or repo `AGENTS.md` as appropriate.

### Completion Standard

- Do not treat agent-surface work as complete while direct validation findings remain unresolved.
```

When a repo can safely depend on this shared library in its local workspace context, `docs/testing.md` may name the specific shared validation prompts directly.

Use `agents/` for repo-local agent-facing prompts, not for reusable shared validation prompts.

At the workspace level, the user-local `Projects/agents/` layer should, where practical, mirror the organization of `astro-agents/` so the override relationship is easier to infer.

Prefer this staged pattern:

- start with a small `agents/` subtree only when the repo actually needs local agent-facing prompts
- keep `docs/testing.md` as the human-facing source of truth for when those local prompts should be used
- mirror the shared prompt-library structure inside `agents/` when it improves clarity

Use `agents/` for:

- repo-specific validation prompts under `agents/validation/`
- repo-specific authoring prompts under `agents/authoring/`
- repo-specific writing prompts under `agents/authoring/writing/`
- repo-specific coding prompts under `agents/authoring/code/`
- repos where local agent-facing prompts are stable and substantial enough to justify their own subtree

## What Belongs In Repo AGENTS.md

- repo purpose and boundaries
- public API constraints
- architecture and ownership rules
- data, schema, or protocol contracts
- repo-specific environment or deployment constraints
- repo-specific test commands and verification expectations
- review priorities specific to the codebase

## What Does Not Belong In Repo AGENTS.md

- private absolute paths to the workspace prompt library
- assumptions that another repo's `AGENTS.md` will always be available
- generic authoring rules already covered by `astro-agents/authoring/*`
- generic coding-style defaults already covered by `astro-agents/authoring/code/*`
- subtree-specific manuscript or document rules that belong in a deeper `AGENTS.md`
- long background documentation better kept in repo docs and referenced from here

## Public-Safe Repo Pattern

Some repos may eventually be public. To keep repo-level `AGENTS.md` files portable:

- do not hardcode absolute paths to the private workspace prompt library
- do not make one repo's `AGENTS.md` depend on another repo's `AGENTS.md`
- use a generic bootstrap line such as `Follow any higher-level workspace prompt-routing instructions when present.`
- keep only repo-local rules in the repo file itself

This allows a private workspace to supply higher-level routing and shared prompt assets without baking private path assumptions into public repositories.

## References

1. [OpenAI, Introducing Codex](https://openai.com/index/introducing-codex/)
2. [agents.md, How to use AGENTS.md](https://agents.md/)
3. [OpenAI, Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)
4. [GitLab Docs, Documentation AGENTS.md](https://docs.gitlab.com/development/documentation/agents_md/)

These sources do not define this workspace's full hierarchy directly. The workspace bootstrap layer, the shared prompt-library layer, the recommended document set, and the section templates are local design choices informed by these sources rather than prescribed by them.
