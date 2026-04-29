# Prompt Writing Review

## Purpose
Use this prompt to review whether agent-facing prompts follow the applicable shared prompt-writing guides.

## Inputs

- target root or target paths to review
- optional focus on writing prompts, writing-guide prompts, coding prompts, shared review files, project-local review files, or all prompts
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the applicable prompts within the requested scope.

## Scope Identification

When running this review:

- identify applicable prompts dynamically from the target root
- exclude human-facing `README.md` and `docs/` files from this review
- use `authoring/agents/agents-md.md` when reviewing `AGENTS.md` files
- use `authoring/agents/writing-prompt.md` when reviewing prompts that define writing or revision behavior under `authoring/agents/` or project-local `agents/authoring/writing/`
- use `authoring/agents/writing-prompt.md` when reviewing prompts under `authoring/writing/`, because those files are still agent-facing writing guides even though they target human-facing writing
- use `authoring/agents/coding-prompt.md` when reviewing prompts under `authoring/code/` or project-local `agents/authoring/code/`
- use `authoring/agents/review-prompt.md` when reviewing prompts under `validation/` or project-local `agents/validation/`, except where a more specific local standard explicitly takes instruction authority
- use `authoring/agents/agents-md.md` when reviewing `AGENTS.md` files under `research-log/`
- use `authoring/agents/base.md` when reviewing mode, file, or operation prompts under `research-log/themes/instructions/`
- use `authoring/agents/base.md` for other project-local prompts under `agents/`
- inspect surrounding local context only when needed to determine prompt role or applicable comparison guide

## Review Criteria

Evaluate prompts against the applicable style guide.

Required review criteria:

- `AGENTS.md` vs `authoring/agents/agents-md.md`
- prompt role clarity versus the applicable guide under `authoring/agents/`
- scope discipline and prompt-type fit
- explicit routing and next-step references when prompts dispatch or narrow the work
- explicit local-boundary statements when broader and local prompts are both intended to matter
- structure proportionality
- output definition and operational clarity
- duplication versus deeper prompt or source-of-truth references
- internal path usage versus any target-local path convention that is explicitly defined

## Exclusions

Do not treat the following as the default task:

- human-facing `README.md` or `docs/` review
- route-structure design review beyond what is needed to judge prompt writing
- application-code review
- broad prompt rewrites without first identifying concrete issues

## Output

Return:

1. A brief overall judgment of the prompt-writing quality within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the applicable style guide
- name the affected path or paths
- explain why the issue matters
- state the recommended revision move
- distinguish direct violations from softer cleanup opportunities

Keep the review focused on whether prompts are written in the right operational style for their role.
When internal file references appear, apply a project-specific path convention only when the target project explicitly defines one.
