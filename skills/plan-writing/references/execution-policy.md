# Execution Policy

Put checks and reviews in the plan file for the work they verify. Each child
plan contains or directly references every check and review, along with the
selected commit policy. The parent plan contains only checks for its own work or
the integrated result.

Distinguish iteration checks from completion gates. Reference project gates
instead of repeating them. Keep component checks with their work and reserve
integration checks for the assembled result.

If work may continue after a completion gate fails, state the exact
continuation route and stopping point.

Do not repeat reviews completed during planning unless later work will
invalidate them. Avoid generic review-after-every-step requirements.

When expensive work depends on a consequential assumption, put a cheap
representative check before it.

## Agent Reviews

If an agent-review policy has not been chosen, present only applicable options
in plain language:

1. **Agent-surface review (`$agent-surface-review`):** agent instructions,
   workflow behavior, affected contracts, and documentation integration.
2. **Documentation-surface review (`$documentation-surface-review`):**
   documentation ownership, organization, audience fit, and completeness.
3. **Code-quality review (`$code-quality-review`):** source architecture,
   contracts, lifecycle, tests, and maintainability.

Any combination may be chosen, including none. For selected reviews, ask
whether they run once or repeat after in-scope corrections until no findings
remain. Propose a narrow scope and milestone from the plan. Keep project checks
separate.

Record the selected review skills, their scopes, milestone, and whether they run
once or repeat. Never add a review merely because the work matches a review
skill or changes a particular file type. If a finding requires work outside the
plan or a new decision, stop the review loop and ask for direction.

Choose the agent-review policy separately from the commit policy.

## Commit Policy

Ask for one commit policy before implementation:

- Do not commit.
- Request human review, then commit after approval.
- Commit automatically after required checks and reviews.

Record the choice in the plan. If it has already been provided, do not ask
again. Do not infer commit authority from authorization to write or implement
the plan. Human checkpoints do not add technical reviews.

Send authorized implementation to `$plan-execution`. A completed plan does not
authorize implementation.
