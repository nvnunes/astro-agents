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

When a request asks to review prompt assets, review `AGENTS.md` files, validate prompt hierarchy, assess layer ownership, evaluate prompt routing, or asks for validation without naming a narrower review:

- use `validation/review/full-agent-surface-review.md` unless the request clearly requires a narrower validation prompt

When the review scope is not specified:

- default to the requested repo or target root, not the whole workspace
- use `validation/review/full-agent-surface-review.md` unless the request clearly requires a narrower validation prompt

## Use Of Shared Validation Prompts

- Use shared validation prompts to evaluate hierarchy design, not application-code quality or generic prose quality.
- More specific repo or subtree `AGENTS.md` files override this folder's shared defaults within their scope.
- Follow local instructions when the review must account for narrower domain, workflow, or document constraints.

## Practical Rule

Use this folder to answer:

- which validation prompt applies
