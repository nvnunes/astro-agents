# AGENTS.md

## Purpose
Use this folder when the task is to select or run a shared validation review for the prompt system, related `AGENTS.md` files, prompt assets, or supporting documentation.

## Validation Prompt Selection

When a request explicitly asks for one of the shared validation reviews:

- use `validation/review/document-writing-review.md` for human-facing docs
- use `validation/review/prompt-writing-review.md` for `AGENTS.md` files and prompt assets
- use `validation/review/hierarchy-behavior-review.md` for routing and hierarchy behavior
- use `validation/review/documentation-architecture-review.md` for document organization and source-of-truth structure
- use `validation/review/full-agent-surface-review.md` for combined review

When a request asks to review human-facing docs or subgroup `README.md` files without naming a review:

- use `validation/review/document-writing-review.md` unless the request clearly focuses on document organization, source-of-truth structure, or cross-document architecture, in which case use `validation/review/documentation-architecture-review.md`

When a request asks to review prompt assets or `AGENTS.md` files without naming a review:

- use `validation/review/prompt-writing-review.md` unless the request clearly focuses on routing, precedence, or hierarchy behavior

When a request asks to validate prompt hierarchy, assess layer ownership, or evaluate prompt routing without naming a review:

- use `validation/review/hierarchy-behavior-review.md` unless the request clearly asks for broader synthesis

When a request asks for validation without naming a narrower review, or asks for a combined review:

- use `validation/review/full-agent-surface-review.md`

When the review scope is not specified:

- default to the requested repo or target root, not the whole workspace
- default to the narrowest review that matches the request; do not broaden a narrower review request into the combined pass by default

## Use Of Shared Validation Prompts

- Use shared validation prompts to evaluate hierarchy design, not application-code quality or generic prose quality.
- More specific repo or subtree `AGENTS.md` files override this folder's shared defaults within their scope.
- Follow local instructions when the review must account for narrower domain, workflow, or document constraints.

## Practical Rule

Use this folder to answer:

- which validation prompt applies
