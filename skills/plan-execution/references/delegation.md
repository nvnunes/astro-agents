# Working With Subagents

When your plan assigns phase/part/task level work to a separate subagent, start
a fresh subagent to execute it and coordinate interaction with the human.

## Start A Subagent

For the next subagent assignment in your plan:

1. Resolve the referenced subagent plan to an absolute path. Do not read it.
2. Start a fresh subagent without passing it the current conversation context.
   Record its runtime handle in the unit's `in progress` execution-record entry
   until the final handoff has been recorded or recovery has concluded.
3. Give it this minimal prompt:

   `Use $plan-execution to execute <absolute-plan-path>. Work in
   <absolute-workspace-path>.`

In addition, include only dynamic context that you believe the subagent may
need:

- uncommitted work or workspace boundaries it must preserve;
- outcomes from earlier work; and
- human decisions that you think affect the assignment.

Do not copy the conversation or a whole earlier handoff, or restate
`$plan-execution` instructions. If no dynamic context applies, send only the
minimal prompt.

If subagents are unavailable, ask the human before doing the assigned work
yourself.

## While The Subagent Works

- Do not repeatedly poll or read the subagent's thread.
- Do not request routine progress, command output, or intermediate reasoning.
- The subagent may send brief milestone updates.
- When no other work can proceed, wait for status in bounded intervals. If a
  wait returns no result, check the retained handle's status before treating
  the delegation as interrupted.
- Wait for a human request, a blocker or failure, or the final handoff.

## Human Input

You own the human-facing thread. When a subagent sends a concise
request for human input through the normal agent channel, present it without
redoing the subagent's work. Return the human's response to the same subagent
session so it can continue owning the assigned work.

## Finish Or Stop

- Trust a successful subagent handoff. Do not inspect its full diff, replay its
  investigation, or rerun its checks.
- Update the corresponding Phase, Part, or Task in your plan's execution record
  from the final handoff.
- If the final handoff reports blocked or failed work, follow any specific
  continuation route in your plan. If none exists, stop.
- Otherwise, continue according to your plan.

## Recover Interrupted Delegation

Use this section when execution stopped unexpectedly before a subagent's
handoff was recorded or acted on. Recover existing work before starting a
replacement.

- Read your plan, current Git and workspace state, and the latest relevant
  execution record entries.
- Resume with the same subagent if possible.
- If a subagent created a commit but returned no handoff, recover from the
  commit and current workspace state. Do not redo the work or its checks.
- If work remains incomplete and the same subagent cannot be resumed, start a
  replacement with concrete recovery instructions only when your plan or the
  human authorizes that route.
