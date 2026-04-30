# SKILL.md

## Purpose
Use this prompt when writing or revising a `SKILL.md` file for an agent skill. Inherit the common prompt-writing discipline from `skills/prompt-writing/references/prompt-base.md` and the writing-prompt discipline from `skills/prompt-writing/references/writing-prompt.md`.

A `SKILL.md` file is agent-facing context for a reusable, on-demand capability. It should make the skill discoverable, explain the workflow the agent should follow after activation, and point to any scripts, references, or assets the skill needs.

## Success Criteria
- Make the skill's job and trigger boundary clear from the frontmatter.
- Keep the skill focused on one reusable capability, procedure, or workflow family.
- Preserve progressive disclosure: metadata for discovery, `SKILL.md` for the activated workflow, and resource files for details loaded only when needed.
- Define required inputs, workflow steps, expected outputs, validation checks, and important failure behavior.
- Use scripts only when deterministic execution, external tooling, consistency, or repeated code would materially improve the skill.
- Respect the target runtime's supported fields, discovery locations, invocation policy, and tool model.
- Keep project policy, project workflow rules, and long background documentation out of `SKILL.md` unless they are necessary to perform the skill.

## Frontmatter
- Include required YAML frontmatter with `name` and `description`.
- Use a short, lowercase, hyphenated `name` when creating a new skill, and keep it aligned with the skill folder name.
- Write the `description` as the primary discovery surface for implicit skill activation.
- Front-load the core use case, task nouns, file types, tools, or domain words that should trigger the skill.
- State the skill's positive trigger boundary and important negative boundary in the `description` when misactivation is likely.
- Do not rely on body sections such as "When to use this skill" to fix an unclear description; the body may not be loaded until after activation.
- Include optional frontmatter fields only when the target skill runtime supports them and they add operational value.

## Body
- Start with the skill's practical job, not with broad background about the domain.
- Use imperative steps that tell the agent what to do after the skill is active.
- Specify required inputs, assumptions, and any information the agent should ask for before proceeding.
- Define expected outputs, file artifacts, or response shape when the skill's result must be consistent.
- Reference another skill explicitly when the workflow should pair with it, and avoid duplicating that other skill's full procedure.
- Include validation or review steps for brittle, high-stakes, or multi-step work.
- Include positive and negative trigger examples only when they clarify real activation ambiguity.
- Include current-doc or web checks only when the skill depends on fast-moving APIs, platform behavior, or runtime tool contracts; make the condition explicit instead of making browsing part of ordinary use.
- Name common edge cases and failure modes only when they affect execution.
- Keep examples short and targeted to real ambiguity.
- Keep the body lean enough to be worth loading during a live task; move detailed schemas, long examples, API notes, and variant-specific guidance into referenced files.

## Bundled Resources
- Use `scripts/` for deterministic, fragile, repetitive, or tool-heavy operations.
- For each script the skill expects the agent to use, state whether to execute it or read it, then provide the command shape, required inputs, expected outputs, and verification behavior.
- Write scripts with clear errors and documented parameters so the agent can repair inputs rather than infer hidden behavior.
- Use `references/` for detailed documentation the agent should read only when a task requires it.
- Link key reference files directly from `SKILL.md`, or use a clearly named reference index when the skill has a larger reference set.
- Explain when to read each linked reference or reference index.
- Keep reference files focused and avoid deep reference chains.
- Use `assets/` for templates, static files, images, boilerplate, or other resources that are used in the final output rather than read as instructions.
- Use runtime-specific metadata files such as `agents/openai.yaml` only when they are supported and needed for UI metadata, invocation policy, or tool dependencies.
- Keep runtime metadata aligned with the skill's frontmatter, trigger boundary, and actual workflow.

## Scope And Placement
- Use a skill when the procedure is reusable across tasks and benefits from on-demand context.
- Prefer a normal prompt, project instruction, or one-off task response when the guidance is not reusable.
- Put project-specific skills in the target runtime's project-local skill location, such as `.agents/skills`, when the workflow belongs to that project.
- Put user-level skills in the target runtime's user-level skill location when the workflow should apply across projects.
- Do not duplicate the whole skill procedure in project instructions; invoke or reference the skill instead.

## Preservation And Revision
When revising a `SKILL.md` file:
- Preserve the skill's intended capability before improving wording.
- Tighten `name` and `description` before adding body complexity when discovery or triggering is unclear.
- Preserve existing scripts, references, assets, and metadata links unless intentionally changing the skill package.
- Remove stale resources and references when they no longer support the active workflow.
- Keep validation and failure-handling instructions explicit.
- Update resource instructions when a script, reference, asset, or runtime metadata file changes.

## Review Checks
When reviewing a `SKILL.md` file, check for:
- weak or misleading trigger description
- scope creep across unrelated workflows
- body content that should be in a reference file
- missing input, output, or validation behavior
- missing positive or negative trigger tests when activation is uncertain
- duplicated sibling-skill workflow where an explicit cross-skill reference would be clearer
- unconditional external-doc or web-check instructions where a conditional current-doc check would be enough
- unclear script execution instructions
- hardcoded tool names or command shapes that no longer match the target runtime
- stale or unreferenced bundled resources
- runtime metadata that no longer matches the `SKILL.md` behavior
- unsupported or unnecessary frontmatter fields
- duplication of project policy or broad documentation better owned elsewhere

## Output
- When writing or revising a `SKILL.md` file, return the revised file content directly unless explanation is requested.
- When reviewing a `SKILL.md` file, identify the highest-impact issues first, name the affected section, and state the corrective edit.
