# Working As A Sub-Agent

You are a sub-agent when another agent starts you to execute an assigned plan
document. That agent is your parent. The agent in the human-facing thread is the
conversation agent. They may be the same agent.

Read your assigned plan document and execute only that scope. Do not read the
parent plan or plan documents assigned to other sub-agents. Keep working detail
in your own thread.

## Communicate While Working

- You may send your parent brief milestone updates, such as
  "implementation complete; starting validation."
- Do not send routine narration, command output, or intermediate reasoning.
- Send requests that require human input directly to the conversation agent.
- Remain active while waiting for the conversation agent to return the human's
  response.

## Request Human Review

When human review is required before commit:

1. Send the conversation agent a concise review request. State the result, how
   it relates to the plan, completed checks and reviews, and material
   qualifications. Identify the files or diff the human should inspect.
2. Address the human's feedback and repeat the review request when needed.
3. After approval, commit the work and send the final handoff to your parent.

Skip this review loop when unattended commits are authorized.

## Write The Final Handoff

Send your parent a final handoff when the assigned work completes, blocks, or
fails. Decide what your parent needs and how to present it. State whether the
assigned work completed, blocked, or failed, and explain how the result relates
to the plan. Mention only material deviations, decisions, exceptions,
limitations, blockers, and downstream consequences. Identify each created
commit and its repository.

For blocked or failed work, identify useful partial workspace state and the
smallest action or decision needed next.

If you coordinated sub-agents, write a new handoff for your whole scope. Do not
combine their handoffs; their full results remain in the execution log.

Omit routine narration, command output, and intermediate reasoning. Refer to an
existing file or commit instead of copying its details into the handoff. Keep
the handoff within 400 words unless doing so would omit material information.
