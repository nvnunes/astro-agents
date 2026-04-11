# Upgrade Review Public Python

## Purpose
Use this prompt to handle the `public-python` review task `review the public documentation surface` and write the saved review artifact under `docs/upgrade/` in the target repo.

Keep the work inside that review task and replace only that task's saved review artifact when you rerun it.

## Inputs

- target root or target paths
- documentation surface profile declared in the target repo's root `AGENTS.md`
- optional focus areas
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

Use this prompt only when the target repo's root `AGENTS.md` explicitly declares `Documentation surface profile: public-python`.

If that explicit `public-python` declaration is missing, stop and send the user to `astro-agents/upgrade/upgrade-documentation-surface-profile.md` or `astro-agents/upgrade/upgrade-review.md` instead of inferring the `public-python` path.

Write or replace `docs/upgrade/review-public-documentation-surface.md` in the target repo.

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` first.
2. Read the target repo's root `AGENTS.md` and confirm that it declares `Documentation surface profile: public-python`.
3. Confirm that the requested task is `review the public documentation surface`.
4. Read the repo files, validation artifacts, and saved `docs/upgrade/edit-*.md` or `docs/upgrade/review-*.md` files needed for the public documentation review.
5. Use `astro-agents/validation/review/public-python/documentation-review.md` as the default shared validation entrypoint.
6. Prefer a higher-precedence local validation layer only when one actually exists in the target repo and explicitly implements the needed `public-python` documentation review.
7. If the repo validation docs indicate that a higher-precedence local validation layer should exist and none is present, use the shared entrypoint and record the missing local validation layer as a review gap.
8. Create `docs/upgrade/` when it does not already exist, then write or replace `docs/upgrade/review-public-documentation-surface.md`.

## Review Guidance

- review the public user and developer documentation surface together
- include other public-facing surfaces such as contributor docs, release docs, and tutorial assets only when they are part of the public documentation surface under review
- report findings when they exist, or say explicitly that no material issues were found
- keep the review grounded in the already exposed public docs surface rather than reopening planning decisions unless a real issue now requires that

## Exclusions

- do not perform editing tasks while reviewing
- do not choose or reinterpret the documentation surface profile
- do not treat this prompt as a substitute for the core review prompt
- do not use this prompt when the requested review is not actually the public documentation surface

## Output

Write or replace `docs/upgrade/review-public-documentation-surface.md` in this structure:

```md
# Review: Public Documentation Surface

## Metadata

- task:
- status: `done` | `blocked`
- documentation surface profile: `public-python`
- prompt used:
- last updated:

## Scope And Oversight

- review scope:
- validation path used:

## Approval

- approval status: `not needed`
- approval reference:

## Output

## Follow-Up
```

Return a short review summary with:

- saved file path
- scope reviewed
- review or validation paths used
- findings or explicit confirmation that no material issues were found
- any recommended follow-up
