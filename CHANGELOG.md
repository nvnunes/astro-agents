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
