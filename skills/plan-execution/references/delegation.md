# Working With Sub-Agents

When your plan assigns a separate plan document to a sub-agent, you are that
sub-agent's parent. Start a fresh sub-agent to execute the document
and record its returned handoff before proceeding.

## Start A Sub-Agent

For the next sub-agent assignment in your plan:

1. Start a fresh sub-agent without inherited conversation turns. Tell it to use
   `$plan-execution` to execute its assigned plan document.
2. Give it the path to its assigned plan document. Do not read that document
   yourself.
3. Add only these items from your plan, current state, human instructions, and
   earlier handoffs:

   - current repository and workspace state, including uncommitted work to
     preserve;
   - outcomes your plan identifies as dependencies of the assigned work;
   - later human decisions that your plan indicates apply to the assigned work,
     including researcher decisions and overrides to review or commit policy;
     and
   - the execution-log path and conversation agent's address.

Do not copy the parent transcript or a whole earlier handoff into the new
context. If sub-agents are unavailable, ask the human before doing the assigned
work yourself.

## While The Sub-Agent Works

- Do not poll or read the sub-agent's thread.
- Do not request routine progress, command output, or intermediate reasoning.
- The sub-agent may periodically send you brief milestone updates.
- Wait for the final handoff.

## Execution Log

If your parent supplied an execution-log path, use it. Otherwise, create the log
when the first sub-agent starts and derive its path from the plan:
`my-plan.md` uses `my-plan/my-plan-log.md`.

Keep the log untracked in Git. Never include it in a commit.

When you receive a handoff:

1. Add `## Handoff: <assignment>`, using the name of the sub-agent assignment
   from your plan.
2. Append the handoff unchanged.

Do not add progress messages, edit earlier entries, or rewrite the handoff. A
later handoff may state that it supersedes an earlier one. You do not edit
the log file other than appending to it.

## Human Input

The conversation agent is the agent in the human-facing thread. All required
human interaction goes through that agent.

If you are the conversation agent, present a sub-agent's request without
redoing its work. Return the human's response to that sub-agent.

If you are not the conversation agent, do not participate in these requests.

## Finish Or Stop

- If the final handoff reports blocked or failed work, follow any specific
  instructions your plan gives for that result. Otherwise, stop.
- Trust a successful sub-agent handoff. Do not inspect its full diff, replay its
  investigation, or rerun its checks.

## Recover Interrupted Delegation

Use this section when execution stopped unexpectedly before a sub-agent's
handoff was recorded or acted on. Recover existing work before starting a
replacement.

- Read your plan, current Git and workspace state, and the latest relevant log
  entries.
- Resume with the same sub-agent if possible.
- If a sub-agent created a commit but its handoff is missing from the log,
  append a short entry identifying the commit. Do not redo the work or its
  checks.
- If work remains incomplete and the same sub-agent cannot be resumed, start
  one replacement with concrete recovery instructions.
- If the replacement cannot complete the work, ask the human for direction.
- Preserve useful partial work. Do not create a checkpoint commit unless the
  plan authorizes one.
