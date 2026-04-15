# AGENTS.md

## Purpose
Use this folder when the task is to choose or run a shared review for the prompt system, related `AGENTS.md` files, prompts, or supporting documentation.

## Choosing A Shared Review

When a request asks to upgrade a repo, review a repo for upgrade readiness, plan or propose how to group the upgrade work from current repo state, or assess a repo against the shared upgrade design:

- use `validation/review/upgrade-review.md`

When a request explicitly asks for one of the shared validation reviews:

- use `validation/review/code-quality-review.md` for current-state source-code quality review through the applicable shared built-in workflow
- use `validation/review/documentation-review.md` for documentation review chosen from the repo's documentation surface profile
- use `validation/review/prompt-writing-review.md` for `AGENTS.md` files and prompts
- use `validation/review/routing-and-scope-review.md` for routing, workflow, and scope behavior
- use `validation/review/full-agent-surface-review.md` for combined review

Treat profile-scoped documentation workflows and reusable internal review files as internal workflow pieces rather than as normal starting documents.

When a request asks to review human-facing docs or folder-level `README.md` files without naming a review:

- use `validation/review/documentation-review.md`

When a request asks to review prompts or `AGENTS.md` files without naming a review:

- use `validation/review/prompt-writing-review.md` unless the request clearly focuses on routing and workflow, scope behavior, explicit local exceptions, or route-structure behavior

When a request asks to review code quality or source-code quality without naming a review:

- use `validation/review/code-quality-review.md`

When a request asks to validate route structure, assess scope ownership, or evaluate prompt routing and workflow without naming a review:

- use `validation/review/routing-and-scope-review.md` unless the request clearly asks for broader synthesis

When a request asks for validation without naming a narrower review, or asks for a combined review:

- use `validation/review/full-agent-surface-review.md`

When the review scope is not specified:

- default to the requested repo or target root, not the whole workspace
- default to the narrowest matching review path; do not broaden a narrower review request into the combined pass by default
- do not choose `validation/review/core-document-writing-review.md` for generic document-writing requests; use `validation/review/documentation-review.md` instead

## Internal Workflow Files

- Use `validation/review/core-document-writing-review.md` only as a reusable internal building block, not as a normal starter review.
- Use files under `validation/review/private-default/` and `validation/review/public-python/` as internal documentation-workflow files unless a narrower implementation task explicitly requires them.
- Use files under `validation/review/python/` as internal code-quality workflow files unless a narrower implementation task explicitly requires them.

## Use Of Shared Review Prompts

- Use shared review prompts for agent surface review and explicit shared code-quality review, not generic prose quality or broad application audits.
- More specific repo or subtree `AGENTS.md` files may apply narrower local review prompts within their scope.
- Follow local instructions when the review must account for narrower domain, workflow, or document constraints.

## Practical Rule

Use this folder to answer:

- which shared review applies
