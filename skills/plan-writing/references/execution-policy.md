# Execution Policy

Place checks immediately after the work they verify. Each child plan contains
or directly references its assigned checks and selected commit policy.

Keep checks and reviews with the executing Task that owns the work. For
delegated phases, assign cross-phase integration checks to the phase that assembles the result. The
coordinating Task dispatches phases and acts on their handoffs; it does not
repeat their checks or reviews.

In the plan that owns the work, place each independent agentic review immediately
after the work it reviews. The executing Task coordinates the reviewer and
handles findings and corrections locally.

Distinguish iteration checks from completion gates. Reference project gates
instead of repeating them. Keep component checks with their work and reserve
integration checks for the assembled result.

If work may continue after a completion gate fails, state the exact
continuation route and stopping point.

Do not repeat reviews completed during planning unless later work will
invalidate them. Avoid generic review-after-every-step requirements.

Before expensive work, test consequential assumptions with a cheap representative
check. If an implementation mistake would cause substantial rework, check a small
working example for design conformance before broadening the implementation.

Choose verification that reaches the claimed behavior and asserts its expected
outcome. Passing test counts, mocks that bypass that behavior, or agreement
between consumers of the same helper do not establish correctness on their own.

## Implementation Ownership

Keep design, implementation-plan refinement, implementation, behavioral tests,
and corrections with one executing Task. Divide its work into cohesive,
verifiable units with recorded decisions and acceptance evidence so it can
resume reliably after context turnover. Work-unit boundaries do not create
agent assignments. Use `references/sharding.md` when considering phase delegation.

## Agentic Reviews

If an agentic review policy has not been chosen, present only applicable options
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

At each review point, state the selected review skills and scope, whether
correction rechecks repeat, and the transition after success. Leave agent and session management,
finding routing, and recovery to `$plan-execution`. Never add a review merely
because the work matches a review skill or changes a particular file type. If a
finding requires work outside the plan or a new decision, stop the review loop
and ask for direction.

Choose the agentic review policy separately from the commit policy.

## Commit Policy

Ask for one commit policy before implementation:

- Do not commit.
- Request review and approval, then commit.
- Commit automatically after required checks and reviews.

Record a policy that applies unchanged throughout once in the orientation;
otherwise place each choice beside the work it governs. In either case, place
each commit action at the transition it controls. Express review-and-approval
checkpoints consistently: identify what is being approved and what that
approval authorizes, and make deliberate differences between checkpoints
explicit. If the policy has already been provided, do not ask again. Do not
infer commit authority from authorization to write or implement the plan. User
checkpoints occur in the Task that owns the work and do not add technical
reviews.

Send authorized implementation to `$plan-execution`. A completed plan does not
authorize implementation.
