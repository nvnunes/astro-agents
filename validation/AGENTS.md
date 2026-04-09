# AGENTS.md

## Purpose
Use this folder when the task is to select or run a shared validation prompt for the prompt system, related `AGENTS.md` files, prompts, or supporting documentation.

## Validation Prompt Selection

When a request explicitly asks for one of the shared validation reviews:

- use `validation/review/documentation-review.md` for documentation review selected from the repo's documentation surface profile
- use `validation/review/core-document-writing-review.md` only when the user explicitly asks for the shared core document-writing review as a reusable component
- use `validation/review/prompt-writing-review.md` for `AGENTS.md` files and prompts
- use `validation/review/hierarchy-behavior-review.md` for routing and hierarchy behavior
- use `validation/review/private-default/document-writing-review.md` for private-default document-writing review
- use `validation/review/private-default/documentation-architecture-review.md` for private-default document organization and source-of-truth structure review
- use `validation/review/private-default/documentation-review.md` for the private-default documentation bundle
- use `validation/review/public-python/document-writing-review.md` for public-Python document-writing review
- use `validation/review/public-python/documentation-architecture-review.md` for public-Python documentation architecture review
- use `validation/review/public-python/documentation-review.md` for the public-Python documentation bundle
- use `validation/review/full-agent-surface-review.md` for combined review

When a request asks to review human-facing docs or folder-level `README.md` files without naming a review:

- use `validation/review/documentation-review.md`

When a request asks to review prompts or `AGENTS.md` files without naming a review:

- use `validation/review/prompt-writing-review.md` unless the request clearly focuses on routing, precedence, or hierarchy behavior

When a request asks to validate prompt hierarchy, assess layer ownership, or evaluate prompt routing without naming a review:

- use `validation/review/hierarchy-behavior-review.md` unless the request clearly asks for broader synthesis

When a request asks for validation without naming a narrower review, or asks for a combined review:

- use `validation/review/full-agent-surface-review.md`

When the review scope is not specified:

- default to the requested repo or target root, not the whole workspace
- default to the narrowest matching review path; do not broaden a narrower review request into the combined pass by default
- do not route generic document-writing requests to `validation/review/core-document-writing-review.md`; use a profile-specific document-writing review instead

## Use Of Shared Validation Prompts

- Use shared validation prompts for agent surface review, not application-code quality or generic prose quality.
- More specific repo or subtree `AGENTS.md` files may activate narrower local validation prompts within their scope.
- Follow local instructions when the review must account for narrower domain, workflow, or document constraints.

## Practical Rule

Use this folder to answer:

- which shared validation prompt applies
