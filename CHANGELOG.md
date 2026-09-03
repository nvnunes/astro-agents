# Changelog

This file tracks notable changes to the public surface of `astro-agents`.

`astro-agents` is pre-1.0. Structural and file-level breaking changes may still
happen while the public surface settles.

## Unreleased

Public release work is still settling. Until the first tagged release, use git
history for detailed change-by-change context.

- Cut research-log validation over to the code-only mechanical engine. The
  public CLI now publishes schema-1 `mechanical.json`, its independent
  disposable cache, and the shared `validation.md` report. Recognized
  unsupported generated metadata produces a no-write
  `validation.unsupported_metadata` result. The legacy semantic continuation,
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
  scopes. Canonical record dependencies now preserve exact decimal locator
  values while remaining valid generated-record JSON.
- Added end-to-end Provenance validation. Entry-root `data.json` v3 declares
  explicit origin boundaries, while `pyrun` maintains output-keyed
  `pyrun-outputs.json` records containing current output and script
  fingerprints, exact ordered parameters, and direct-input fingerprints.
  `pyrun` can also capture stdout, stderr, or a merged stream as a retained
  output without relying on shell redirection.
- Kept `orphan` as the schema-1 machine scope while using Hygiene as the
  human-facing category for orphan artifacts, unmatched output records, and
  unused input declarations. Missing graph-declared outputs instead fail
  Provenance. Rules-version changes invalidate cached checks while preserving
  compatible artifact identities.
- Zero-dimensional NPZ members requested as aligned arrays now produce a
  precise type-mismatch finding instead of terminating validation.
