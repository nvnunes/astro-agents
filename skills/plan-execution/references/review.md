# Coordinate Agentic Review

When your plan requires agentic review of your implementation:

1. Keep the implementation unit `in progress` until its reviews and commit
   policy are satisfied.
2. Start a fresh subagent with the assigned review skill and scope. Identify
   the workspace and files or diff to inspect. Do not pass conversation
   context, an execution plan, or `$plan-execution`. Retain its runtime handle
   in your execution record.
3. Address in-scope findings in your own Task.
4. If the review policy requires a repeat, review the corrected work again.
   Use the same reviewer session.
5. When the review passes, follow the human-review and commit policy.

If the review blocks, fails, or requires work outside the plan, follow any
specific continuation route in your plan. If none exists, stop the assigned
work and send the main Task a blocked or failed handoff.
