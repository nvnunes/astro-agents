# Shared Validation Library

This folder contains shared reviews and related validation workflows.

Use it to understand the shared reviews in this repo, what each one is for, and which guide or review path to use next.

For the repo-wide `AGENTS.md` / `README.md` / prompt role model, use `docs/architecture.md`.

## Public Review Entrypoints

Use these as the normal directly user-addressable shared review entrypoints:

- `validation/review/code-quality-review.md`
  - prompt that chooses the applicable shared built-in code-quality workflow for the requested current-state source-code scope
- `validation/review/full-agent-surface-review.md`
  - coordinating review file for a combined review of prompt-writing quality, routing and scope behavior, and the applicable documentation-review workflow
- `validation/review/documentation-review.md`
  - prompt that chooses documentation review from the repo's documentation surface profile
- `validation/review/prompt-writing-review.md`
  - focused review of `AGENTS.md` and other agent-facing prompts against the applicable prompt-writing guides
- `validation/review/routing-and-scope-review.md`
  - focused review of routing discipline, scope behavior, folder coherence, and prompt role drift

## Upgrade-Specific Path

- `validation/review/upgrade-review.md`
  - shared review file for assessing a repo against `docs/upgrade-design.md`, recommending a documentation surface profile, and suggesting how to group the work

## Internal Workflow Files

- `validation/base-testing.md`
  - shared base testing guidance for agent-surface validation, review selection, completion standards, and regression priorities
- `validation/review/core-document-writing-review.md`
  - shared writing-review file used internally by profile-specific document-writing workflows after they choose scope
- `validation/review/private-default/documentation-review.md`
  - implicit-default documentation workflow used internally when no non-default documentation surface profile is declared
- `validation/review/private-default/document-writing-review.md`
  - private-default document-writing step used within that workflow
- `validation/review/private-default/documentation-architecture-review.md`
  - private-default documentation-architecture step used within that workflow
- `validation/review/public-python/documentation-review.md`
  - documentation workflow used internally for repos that declare `documentation surface profile: public-python`
- `validation/review/public-python/document-writing-review.md`
  - public-Python document-writing step used within that workflow
- `validation/review/public-python/documentation-architecture-review.md`
  - public-Python documentation-architecture step used within that workflow
- `validation/review/python/code-quality-review.md`
  - Python code-quality workflow used internally by the shared code-quality review

In this folder:

- `validation/base-testing.md`
  - defines the shared validation baseline that downstream repos can import into `docs/testing.md`
- the public shared review surface is the five entrypoints listed above
- shared selector and combined-review outputs should include a short `Route Summary` showing the active review path
- upgrade review stays user-facing, but as a separate upgrade-specific path rather than as part of the core public review set
- the shared validation library currently provides these documentation surface profiles through internal workflows:
  - implicit `private-default`
  - explicit `public-python`
- the shared validation library currently provides these built-in code-quality workflows:
  - explicit `python`
- other documentation surface profiles may be implemented by explicitly provided workspace- or repo-local review files

## Review Independence

The public review entrypoints under `validation/review/` are intended to be independently triggerable.

In this folder:

- a starter request for a narrower public review should invoke only that review by default
- a narrower review may mention adjacent issues only when needed to judge its own review criteria
- broader synthesis belongs in `validation/review/full-agent-surface-review.md`, not in the narrower reviews
- if a narrower review repeatedly needs broader scoping to be useful, treat that as a validation-design problem rather than silently broadening the starter request
- `validation/review/core-document-writing-review.md` is a shared building block, not a starter request for generic docs review
- profile-specific documentation workflows and language-specific code-quality workflows are internal workflow files, not normal starter requests

## Starter Requests

Use these short prompts in fresh threads when you want the validation dispatcher to invoke a public shared review entrypoint with minimal extra scoping.

- `Do a full agent surface review.`
  - intended to trigger `validation/review/full-agent-surface-review.md` and return one combined assessment across the repo's full agent surface
- `Do a code quality review.`
  - intended to trigger `validation/review/code-quality-review.md` and select the shared Python workflow when the requested scope is clearly Python
- `Review this repository's docs using the shared documentation review.`
  - intended to trigger `validation/review/documentation-review.md` and determine the repo's declared documentation surface profile, or `private-default` when none is declared
- `Review this repository's AGENTS.md files and prompts using the shared prompt-writing review.`
  - intended to trigger `validation/review/prompt-writing-review.md`
- `Review this repository's prompt routing, workflow, and scope behavior using the shared routing-and-scope review.`
  - intended to trigger `validation/review/routing-and-scope-review.md`

## Upgrade Starter Requests

Use these when you want the upgrade-specific shared path rather than one of the public review entrypoints above.

- `Upgrade this repository using the shared upgrade review.`
  - intended to trigger `validation/review/upgrade-review.md` and start from the review-first upgrade path
- `Review this repository for upgrade readiness using the shared upgrade review.`
  - intended to trigger `validation/review/upgrade-review.md` and return a recommended way to group the work plus a recommended documentation surface profile
- `How should I split up the upgrade work for this repository?`
  - intended to trigger `validation/review/upgrade-review.md` and return upgrade recommendations and a suggested way to group the work

For shared testing-baseline guidance rather than a current-state review, start with `validation/base-testing.md`.

## Writing Shared Reviews

For the writing style used by the review files in this folder, use `authoring/agents/review-prompt.md`.

That guide inherits the common prompt-writing discipline from `authoring/agents/base.md`.

## Design Reference

For the shared `README.md` / `AGENTS.md` rationale behind this folder structure, use `docs/architecture.md`.
