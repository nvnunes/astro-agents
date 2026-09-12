# Coordinate Agentic Review

When your plan requires agentic review of your implementation:

1. Keep the implementation unit `in progress` until its reviews and commit
   policy are satisfied.
2. Start a fresh subagent for independent review with the assigned skill,
   scope, and source-of-truth contracts; do not pass conversation context,
   an execution plan, or `$plan-execution`. Identify the workspace and files
   or diff, and retain the reviewer's runtime handle in your execution record.

   Review a stable version of the assigned files or diff. Keep that scope
   unchanged during the pass, or provide an immutable snapshot; unrelated work
   may continue. If the reviewed state changes, identify the affected findings
   and have the reviewer recheck them against the current state before accepting
   the review.
3. Address in-scope findings in your own Task. For behavioral defects, follow the
   applicable implementation skill's reproduction and regression-test guidance;
   for Python, use `$python-code-writing`.
4. If the review policy requires a repeat, review the corrected work again.
   Use the same reviewer session.
5. When the review passes, follow the human-review and commit policy.

If the review blocks, fails, or requires work outside the plan, follow any
specific continuation route in your plan. If none exists, stop the assigned
work and send the main Task a blocked or failed handoff.
