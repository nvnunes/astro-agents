# Working With Delegated Tasks

When your plan assigns phase, part, or plan task work to a separate Task, start
a fresh Task to execute it. The Task owns its reviews and human interaction.

## Start A Delegated Task

For the next delegated Task assignment in your plan:

1. Resolve the referenced child plan to an absolute path. Do not read it.
2. Start the explicitly authorized Task without passing it the current
   conversation context. Follow the Task tool's workspace rules and ensure its
   workspace can access the plan and required inputs. Record its Task ID in
   the unit's `in progress` execution-record entry.
3. Give it this minimal prompt:

   `You are a delegated Task. Use $plan-execution to execute
   <absolute-plan-path>. Work in <absolute-workspace-path>. Send the final
   handoff to the main Task <task-id>.`

In addition, include only dynamic context that you believe the Task may
need:

- uncommitted work or workspace boundaries it must preserve;
- outcomes from earlier work; and
- human decisions that you think affect the assignment.

Do not copy the conversation or a whole earlier handoff, or restate
`$plan-execution` instructions. If no dynamic context applies, send only the
minimal prompt.

If Tasks are unavailable, ask the human before doing the assigned work
yourself.

## While The Delegated Task Works

- Check delegated Task status every five minutes by default, unless the human
  specifies a different interval. Do not make additional routine status checks
  between intervals. Act on incoming messages and final handoffs when they arrive.
- Do not request routine progress, command output, or intermediate reasoning.
- The Task may send brief milestone updates.
- A finished turn is not a final handoff.

## Finish Or Stop

- Trust a successful Task handoff. Do not inspect its full diff, replay its
  investigation, or rerun its checks.
- Update the corresponding phase, part, or plan task in your execution record
  from the final handoff.
- If the final handoff reports blocked or failed work, follow any specific
  continuation route in your plan. If none exists, stop.
- Otherwise, continue according to your plan.

## Recover Interrupted Delegation

Use this section when execution stopped unexpectedly before a Task's
handoff was recorded or acted on. Recover existing work before starting a
replacement.

- Read your plan, current Git and workspace state, and the latest relevant
  execution record entries.
- Resume with the same Task if possible.
- If a Task created a commit but returned no handoff, recover from the
  commit and current workspace state. Do not redo the work or its checks.
- If work remains incomplete and the same Task cannot be resumed, start a
  replacement with concrete recovery instructions only when your plan or the
  human authorizes that route.
