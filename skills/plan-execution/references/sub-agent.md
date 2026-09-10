# Work On Your Assigned Plan

Read your assigned plan and execute only that scope. Do not read other plans.
Keep working detail in your own thread. The main agent owns the human-facing
thread and its own plan.

## Communicate While Working

- You may send the main agent brief milestone updates, such as
  "implementation complete; starting validation."
- Do not send routine narration, command output, or intermediate reasoning.
- Send requests that require human input to the main agent.

## Request Human Input

When your work requires human input:

1. Send the main agent one concise request containing the information the human
   needs to decide.
2. Do not continue the gated work or send a terminal handoff while waiting.
3. Resume the work when the main agent returns the human's response.

## Request Human Review

When human review is required before commit:

1. Send the main agent a concise review request. State the result, how it
   relates to the plan, completed checks and reviews, and material
   qualifications. Identify the files or diff the human should inspect.
2. Address the human's feedback and repeat the review request when needed.
3. After approval, follow your plan's commit policy and send the final handoff
   to the main agent.

Skip this review loop when unattended commits are authorized.

## Write The Final Handoff

Send the main agent a final handoff that identifies the assigned work as
completed, blocked, or failed.

- For completed work, identify any material deviation, decision, exception,
  limitation, or downstream consequence. If there are none, state that the work
  completed as planned.
- State whether the prescribed checks and reviews passed, failed, or were not
  run.
- For blocked or failed work, identify useful partial workspace state, the
  cause, and the smallest action or decision needed next.
- Identify each created commit and its repository.
- Refer to existing files, commits, or other durable artifacts instead of
  copying their contents.

Omit routine narration, command output, and intermediate reasoning. Keep the
handoff within 400 words unless doing so would omit material information.
