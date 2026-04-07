# Validation

This document records the reusable validation bootstrap prompts that `astro-agents` has preserved so far.

Use it when you want a short prompt that should trigger one of the shared validation reviews documented here in a fresh thread without carrying extra scope instructions by hand.

## Purpose

Use this document to record:

- short bootstrap prompts that reliably trigger the intended shared validation path
- the expected validation target when a bootstrap prompt is used
- notes about what each bootstrap prompt is and is not meant to review

Keep validation requirements, canonical checks, and completion rules in `docs/testing.md`.

## Full Agent-Surface Review

Bootstrap prompt:

`Do a full agent-surface review.`

Intended effect:

- trigger `validation/review/full-agent-surface-review.md`
- run a combined review across document-writing quality, prompt-writing quality, hierarchy behavior, and documentation architecture within the requested repo or target root
- add applicable repo-local overlays under `agents/validation/` after the shared combined review
- synthesize overlapping findings into one combined assessment instead of returning isolated narrow-review reports

Use this when:

- you want one combined validation pass across the repo’s full agent surface
- you want to test whether the composite validation router and full review are discoverable from minimal input

## Document Writing Review

Bootstrap prompt:

`Review this repository’s human-facing docs using the shared document writing review prompt.`

Intended effect:

- trigger `validation/review/document-writing-review.md`
- review `README.md`, subgroup `README.md` files, and other human-facing docs
- avoid hierarchy-behavior review or documentation-architecture review except where needed to judge writing quality

Use this when:

- you want a fresh-thread writing review of the human-facing documentation surface covered by `validation/review/document-writing-review.md`
- you want to test whether the validation router and writing-review prompt are discoverable from minimal input

## Prompt Writing Review

Bootstrap prompt:

`Review this repository’s AGENTS.md files and prompt assets using the shared prompt writing review prompt.`

Intended effect:

- trigger `validation/review/prompt-writing-review.md`
- review `AGENTS.md` files and prompt assets under `authoring/`, `validation/`, and repo-local `agents/`
- avoid document-writing review, hierarchy-behavior review, or documentation-architecture review except where needed to judge prompt-writing quality

Use this when:

- you want a fresh-thread review of prompt-writing quality across the repo’s agent-facing surfaces
- you want to test whether the validation router and prompt-writing review are discoverable from minimal input

## Hierarchy Behavior Review

Bootstrap prompt:

`Review this repository’s prompt-routing and hierarchy behavior using the shared hierarchy behavior review prompt.`

Intended effect:

- trigger `validation/review/hierarchy-behavior-review.md`
- review router discipline, hierarchy behavior, subgroup coherence, and prompt scope drift within the requested repo or target root
- avoid document-writing review, prompt-writing review, or documentation-architecture review except where needed to judge hierarchy behavior

Use this when:

- you want a fresh-thread review of prompt-routing behavior and hierarchy coherence across the repo’s agent surface
- you want to test whether the validation router and hierarchy-behavior review are discoverable from minimal input

## Documentation Architecture Review

Bootstrap prompt:

`Review this repository’s documentation architecture using the shared documentation architecture review prompt.`

Intended effect:

- trigger `validation/review/documentation-architecture-review.md`
- review document organization, source-of-truth surfacing, cross-document consistency, and public-safe portability within the requested repo or target root
- avoid document-writing review, prompt-writing review, or hierarchy-behavior review except where needed to judge documentation architecture

Use this when:

- you want a fresh-thread review of how the repo’s documents are split, surfaced, and related across the agent surface
- you want to test whether the validation router and documentation-architecture review are discoverable from minimal input

## Notes

- Bootstrap prompts for narrower reviews should trigger only the intended narrower review by default.
- If a bootstrap repeatedly needs extra manual scoping to work correctly, treat that as a validation-system smell rather than growing the bootstrap text by default.
- Keep bootstrap prompts short enough that they test the prompt system rather than replacing it.
- Add a new bootstrap prompt here only when it has been used successfully enough to be worth preserving.
