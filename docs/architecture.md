# Architecture

This document is the human-facing source of truth for the `astro-agents` project structure and scope model. Use it when designing or revising the library itself, or when reasoning about how the library fits into a broader shared working environment.

Use `docs/glossary.md` for shared local vocabulary such as `agent surface`, `documentation surface`, `documentation surface profile`, and `source of truth`. Use `docs/runtime-model.md` for runtime and control-flow terms such as `route`, `handoff`, `dispatcher`, `selector`, `orchestrator`, `prompt`, `instructions`, and `context`.

Use `docs/usage.md` when applying this library in another project or workspace.

## Architecture Model

`astro-agents` is a shared library. It separates three kinds of reusable material:

- `skills/`: user-facing runtime capabilities packaged as `SKILL.md` plus references and scripts
- `examples/`: example downstream project documents
- `docs/`: source-of-truth docs for `astro-agents` itself

This split keeps runtime capabilities, downstream examples, and project-owned
design docs from collapsing into one instruction surface.

## Skills As Runtime Capabilities

The user-facing capability surface lives in `skills/`.

Each skill package should follow the OpenAI skill pattern:

- `SKILL.md` is required and contains `name` and `description` frontmatter
- the frontmatter description is the primary discovery and trigger surface
- the body stays compact and procedural
- detailed guidance lives in directly linked `references/`
- deterministic helper code lives in `scripts/`
- output resources such as templates or static assets live in `assets/` when needed
- every user-facing skill package includes `agents/openai.yaml` metadata, and that metadata should stay aligned with `SKILL.md`

Current user-facing skills include the `technical-writing` base skill, narrower writing skills, Python code writing, research logging, documentation-surface review, agent-surface review, code-quality review, and project-upgrade planning.

Shared runtime behavior should be represented as a skill or kept inside the owning skill package. Avoid hidden cross-skill shared references for normal skill operation.

## Docs As Source Of Truth

The `docs/` family owns durable source-of-truth material for `astro-agents` itself.

Use docs for architecture, usage, testing, glossary, runtime vocabulary, and
future design notes.

Research logging has three active surfaces with separate authority and one
target reproduction contract:

- `skills/research-logging/` is the runtime surface containing
  the operational and authoring instructions used by agents. It is
  self-documenting and owns agent behavior.
- `docs/research-log-mechanical-validator-spec.md` is the normative
  implementation contract that the mechanical-validation CLI and its
  supporting tools must follow.
- `docs/research-logging.md` is human-facing researcher documentation. It
  explains how researchers use the skill, what they should expect from it, and
  which research decisions remain theirs.
- `docs/research-log-reproduction-spec.md` is the target normative contract for
  command-oriented execution state and mechanical reproduction. Its status
  section defines the prerequisite and cutover boundary; it is not an active
  runtime contract before that cutover.

These surfaces must remain conceptually compatible, but they are not mirrors.
The human guide is not a specification or completeness checklist for the agent
surface, validation tools, or reproduction tools.

Repair has the sole explicit repository-level dependency in the agent surface:
when malformed or legacy research-owned state prevents the owning CLI action
from operating, its focused prompt may progressively consult only the relevant
section of the mechanical-validator specification. Ordinary operations use
only bundled skill guidance and do not load that specification.

## AGENTS.md As Project Brief

The root `AGENTS.md` is the operational working brief for this project. It provides project-local context, source-of-truth pointers, and validation expectations for work inside `astro-agents`.

It is not the primary selector for the reusable skill library. Runtime skill
discovery exposes `astro-agents` skills through their `SKILL.md` descriptions
and metadata, and the model selects applicable skills. The root `AGENTS.md`
should not recreate that skill-selection table.

In practice:

- keep immediate project-local working context and validation expectations in `AGENTS.md`
- keep project overview and starting document guidance in `README.md`
- keep durable design and maintenance expectations in `docs/`
- keep reusable capability procedures in `skills/`
- keep downstream examples in `examples/`

The goal is to make the right information easy to find and hard to misapply.

## Library Structure

At the project root:

- `AGENTS.md`
  - agent-facing working brief for this project
- `README.md`
  - human starting document for library overview and navigation
- `skills/`
  - canonical reusable capability surface
- `examples/`
  - downstream adoption examples
- `docs/`
  - source-of-truth docs for `astro-agents`
- `CHANGELOG.md`
  - public change history

`docs/future/` holds research and design material for later runtime work. Keep it out of the normal onboarding path unless you are working on that future design directly.

## Starting Documents

- `README.md`
  - main human starting document
- `AGENTS.md`
  - main agent starting document
- `docs/usage.md`
  - how to apply this library in other projects and workspaces
- `docs/testing.md`
  - validation requirements for changes inside `astro-agents`
- `docs/research-logging.md`
  - researcher-facing research-log workflow and responsibilities
- `docs/research-log-mechanical-validator-spec.md`
  - normative mechanical-validator implementation contract
- `skills/project-upgrade-planning/references/upgrade-model.md`
  - shared upgrade model for downstream project upgrades
- `examples/downstream-testing.md`
  - example downstream `docs/testing.md`
- `skills/`
  - user-facing reusable capabilities

## Bootstrap Model

At user-global or project entry, a root `AGENTS.md` file should act as a bootstrap file. It establishes the immediate scope, points to the next starting document or skill, and surfaces any local constraints that must be known before work begins.

Within that model:

- `$CODEX_HOME/AGENTS.md` is the canonical global bootstrap file when a user wants `astro-agents` available by default across projects
- a project root `AGENTS.md` is the bootstrap file for project-specific adoption or for project-specific exceptions to a global default
- bootstrap files should stay brief and focus on immediate scope, the next document or skill, and any local constraints needed before work begins

Use `docs/usage.md` for recommended bootstrap snippets.

## Path Convention

In project-facing docs, skills, and instruction files in this project, prefer project-root-relative paths for internal file references.

Within this project, use forms such as:

- `docs/architecture.md`
- `skills/agents-md-writing/SKILL.md`
- `skills/agent-surface-review/SKILL.md`
- `skills/python-code-writing/SKILL.md`

Use skill invocation names when the instruction is telling a user or agent to
invoke a skill:

- `$skill-md-writing`
- `$agent-surface-review`

Use file paths when the instruction identifies a bundled file as evidence, a
comparison standard, or an internal reference to inspect:

- `skills/skill-md-writing/references/skill-md.md`
- `skills/agent-surface-review/references/scope-and-workflow-review.md`

Do not use a file path when the intended meaning is skill invocation.

In the agent-facing files of other projects:

- prefer generic skill-invocation wording when the local setup already makes `astro-agents` available by name
- use explicit `astro-agents/...` references when the local setup intentionally depends on this project as a named shared library
- use `astro-agents/examples/...` references only when a downstream project wants
  to copy or adapt an example document

## Scope Ownership

Use each scope in the broader context system for a distinct kind of instruction:

- `$CODEX_HOME/AGENTS.md`
  - global bootstrap that can direct agents into shared skills across projects
- `<astro-agents-path>`
  - the shared library project
- `<astro-agents-path>/skills`
  - reusable user-facing capability packages
- `<astro-agents-path>/examples`
  - example downstream project documents
- `<project>/AGENTS.md`
  - project-specific bootstrap, architecture pointers, workflow commands, testing expectations, deployment or environment rules, and review priorities
- `<project>/agents` or project-local skills
  - reusable prompts or skills that are too specific for the shared library
- `<project>/<subtree>/AGENTS.md`
  - narrow local instructions or bounded-choice behavior tied to a subtree
- project-local source-of-truth docs
  - architecture, contracts, testing, development workflow, API rules, data rules, and local exceptions

When deciding where a rule belongs:

- if it is a reusable capability that should load on demand, put it in `skills/`
- if it is a source-of-truth model used by one review skill, put it in that skill's `references/`
- if it is an example downstream document, put it in `examples/`
- if it explains how `astro-agents` itself works, put it in `docs/`
- if it depends on one downstream project's architecture, API, testing strategy, deployment path, or domain contracts, keep it in that project's source-of-truth docs or root `AGENTS.md`
- if it matters only inside one subtree, keep it in that subtree's `AGENTS.md` or source-of-truth docs

## Research-Log Validation Architecture

This section defines the architectural invariants that constrain future
research-log validation development. It does not describe the validation
workflow or its implementation. Use `docs/research-logging.md` for the
researcher-facing behavior, and use the `research-logging` skill for the
validation-agent operational procedure. Use
`docs/research-log-mechanical-validator-spec.md` for the normative validator
implementation and generated-artifact contract.

Future validation changes must preserve these invariants:

- **Independent logs:** Validate each research log independently.
- **Observational validation:** A log may change while validation is in
  progress. Validation reflects the information observed when each check was
  performed.
- **Origin evidence:** Evidence may extend across logs. Declare the locally
  accessible material as an origin of the consuming log without importing the
  other log's graph or adding repository coordination requirements.
- **Explicit uncertainty:** Missing, inaccessible, or ambiguous evidence must
  remain visible and must not be treated as successful validation.
- **Completed publication:** Publish a coherent completed mechanical bundle.
  Incomplete evaluation does not replace the prior completed bundle, and an
  ordinary publication failure restores it.
- **Coherent results:** Report completion only when the human-facing result and
  its supporting validation artifacts agree. Missing or stale derived state
  must not make uncertain work appear complete.
- **Safe reuse:** Reuse a prior passing check only when the current rules,
  dependency projection, and complete check agree exactly. Cache state is
  disposable and may never change a conclusion. Add metadata or hashing
  shortcuts only when retained measurements justify the extra currentness
  machinery.
- **Proportional cost:** Validation time should grow approximately linearly
  with the evidence examined wherever possible.
- **On-disk state:** Validation is source-control agnostic and validates the
  research material currently present on disk.
- **Code-only mechanical scope:** Mechanical validation uses deterministic
  code and precise authored metadata. Semantic review and reproduction are
  separate workflows with separate ownership.
- **End-to-end Provenance:** A passing evidence-rooted chain identifies the
  retained artifact, reaches explicit origins through unique producers, and
  matches each generated output to current output and script fingerprints,
  exact ordered parameters, direct-input fingerprints, and observed log-local
  Python code fingerprints recorded by `pyrun`. Associated code support joins
  the material graph independently of confirmation so Hygiene does not
  duplicate a Provenance failure. This is a bounded support claim, not
  causation, scientific validity, or reproduction.
- **Command-owned execution state:** entry-root `pyrun.json` records one stable
  execution identity for each exact command recipe, including its complete
  output set, observed inputs and code, confirmation state, latest run time,
  and slow classification. One shell loop produces one execution identity per
  child `pyrun` invocation. Validation reads this state but does not write it;
  Reproduction executes it directly without using Markdown as authority. The
  bounded historical migration is defined by
  `docs/research-log-reproduction-spec.md`.
- **Split Reorganize ownership:** agents own semantic partitioning, Markdown,
  links, record selection, and support-file movement. The `log reorganize`
  family owns only verified closed identity changes and coordinated authored
  registry updates within one maintained log.
- **Separate write ownership:** Validation agents manage validation artifacts
  and do not modify research-log entries, scripts, or artifacts. Research
  agents manage research material and do not modify validation or reproduction
  artifacts. Reproduction agents manage only generated reproduction state and
  never promote staged outputs into retained research material automatically.
- **Low-cost evolution:** Generated record and cache schemas evolve
  independently. Cache state is disposable; authored evidence-format changes
  use an explicit upgrade rather than compatibility branches in validation.

## Validation

Use skills as the primary way to review the agent surface, code quality, and project upgrades:

- `skills/agent-surface-review/SKILL.md`
  - combined review of prompts, `AGENTS.md`, `SKILL.md`, instruction scope, workflow behavior, documentation-surface review output, and project-local validation expectations
- `skills/documentation-surface-review/SKILL.md`
  - documentation surface profile selection, profile-scoped documentation review, and documentation completion checks
- `skills/code-quality-review/SKILL.md`
  - current-state source-code quality review
- `skills/project-upgrade-planning/SKILL.md`
  - review-led upgrade planning against `skills/project-upgrade-planning/references/upgrade-model.md`

Use `docs/testing.md` for the completion bar and validation requirements.

## Maintenance Expectations

Treat skills, `AGENTS.md`, source-of-truth docs, and downstream examples as
maintained operational infrastructure. Keep them current as the project evolves.

- update `AGENTS.md` when project-local working constraints, source-of-truth pointers, or validation expectations change
- update `SKILL.md` files when a reusable capability's trigger boundary or workflow changes
- update skill references when detailed procedures, review criteria, or examples change
- update architecture docs when boundaries, ownership, or extension points change
- update testing docs when validation expectations change
- remove or revise stale instructions instead of layering new guidance on top of them

Once agents begin to rely on these documents, stale guidance is often worse than missing guidance.
