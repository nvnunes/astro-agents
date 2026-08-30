# Changelog

This file tracks notable changes to the public surface of `astro-agents`.

`astro-agents` is pre-1.0. Structural and file-level breaking changes may still
happen while the public surface settles.

## Unreleased

Public release work is still settling. Until the first tagged release, use git
history for detailed change-by-change context.

- Cut research-log validation over to the code-only mechanical engine. The
  public CLI now publishes schema-1 `mechanical.json`, its independent
  disposable cache, and the shared `validation.md` report; logs containing v1
  evidence or recognized legacy generated state receive a no-write
  `validation.upgrade_required` result. The legacy semantic continuation,
  adjudication, integrated reproduction, and sharded validation-state surfaces
  have been removed.
- Expanded research-log command discovery over a bounded, non-executing static
  shell subset: finite literal `for` loops, locally defined functions invoked
  with literal arguments, loop-local literal `case` assignments, supported
  local substitutions, and scheduling-only `&` and `wait`. Concrete expanded
  invocations receive normal identity, annotation, relationship, and
  resource-bound accounting; unsupported, dynamic, unbound, nested, or
  over-bound constructs remain fail-closed.
- Aligned entry-section classification with the documented label contract:
  experimental sections require `Steps:` and `Results:`, synthesis sections
  require `Findings:`, structurally invalid combinations are reported and
  skipped, and association and summary failures use their normative codes and
  scopes.
