# Maintain Or Revise Your Plan

Create or maintain a top-level `## Execution Record` in your plan:

- Use top-level entries for Phases and nest their Parts and Tasks beneath them.
  For a plan without those levels, use its highest-level work units.
- Identify each recorded unit by its plan identifier and title. Use only these
  states: `not started`, `in progress`, `completed`, `blocked`, or
  `failed`.
- Keep one current entry for each recorded unit and update it in place as work
  advances.
- Set a unit to `in progress` before beginning its work. On resumption, recover
  an `in progress` unit from the execution record and current workspace state
  before starting it again.
- For delegated work, keep the active subagent's runtime handle in the
  `in progress` entry until its final handoff is recorded or recovery concludes.
- Record the material outcome, prescribed checks and reviews, and any created
  commits or durable artifacts. For blocked work, record the smallest action or
  decision needed next. Link to detail rather than copying it, and do not add
  chronological progress narration.

Record decisions and constraints that affect remaining work beside that work
in your plan. Do not read or edit other plan documents, including sharded
Phase, Part, or Task plans, to propagate them.

The execution record reports state. It does not change scope, requirements,
execution order, review or commit policy, or subagent assignments.

If execution requires changing the planned scope, order, requirements,
structure, or subagent assignments, stop and use `$plan-writing`.
