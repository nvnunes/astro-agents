# Material Input Instructions

Use this file when a recorded command consumes a file, directory, or pinned Git
repository; an evidence presentation consumes a file or directory; or the
researcher must choose where its Provenance chain stops. The public `log data`
actions own input-registry validation and storage.
Never create, inspect, or edit the registry during ordinary Record.

Every material input has one stable entry-scoped name. Recorded commands and
evidence use `<name>` instead of a raw path. Use
`<directory-name>/member` for one exact file inside a registered directory.
Do not use a raw relative or absolute path, URI, bare directory, or cross-entry
shorthand as an evidence source.

## Choose The Boundary

- Choose an origin when the target has no producer in this maintained log and
  the researcher intends Provenance to stop at its current bytes. Ask whether
  an accessible external input should be copied into the entry or referenced
  at its current local location.
- Choose generated only after a `pyrun` command in the same maintained log has
  successfully produced and confirmed the current target. Production in
  another maintained log crosses an origin boundary.

Storage location does not determine this choice. Do not infer an origin merely
because no producer was found, and do not hide a known same-log producer behind
an origin boundary.

## Register And Maintain Inputs

Resolve `<skill>/scripts/log` from this skill package and read only the
selected action's help. `<log>` is the logical base whose summary is
`<log>.md`; do not pass the summary file.

```text
<skill>/scripts/log data add-origin --path <log> --entry <entry-id> \
  <name> <target> [--commit <full-commit-hash>]
<skill>/scripts/log data add-generated --path <log> --entry <entry-id> \
  <name> <target>
```

Write `<target>` as an absolute path or a path relative to the selected entry
root, regardless of the shell's current directory. For entry-owned material,
prefer the short entry-relative form such as `data/metrics.json`.

The action infers file versus directory unless `--commit` selects a Git
repository, normalizes the target, records its current identity, verifies the
asserted boundary, and publishes canonical state. After success, use the token
without opening the registry.

For source code identified by a repository commit, use `add-origin --commit`
with the repository root as `<target>` and an exact lowercase 40-character
commit hash. Pass both `<name>` for the repository locator and `<name:commit>`
for the pinned commit to every consuming `pyrun` command. Together they form
one material input. The commit covers only its tracked snapshot; register any
dirty or untracked file, live environment, generated model, cache, build
product, or submodule checkout separately when the command consumes it.

Use the corresponding action for later intent:

- `log data update` changes an explicitly named target or origin/generated
  boundary;
- `log data refresh` records an intentional byte change after rechecking the
  same boundary;
- `log data rename` runs only after every recorded-command token is updated;
  it also changes same-entry evidence source tokens and reports producer
  commands that must be rerun;
- `log data remove` runs only after command and evidence use is removed; and
- `log data list` returns a bounded semantic inventory when needed.

Use action-specific `--dry-run` when a mutation needs preflight. Advanced
origin-directory identity options belong to the selected action's help and
explicit researcher intent; do not load or reproduce their registry
representation during ordinary Record.

A generated output belongs in the input registry only when a later recorded
command or evidence presentation consumes it. Output-only results, scripts,
command logs, and images remain absent unless they later become material
inputs. A directly presented historical non-`pyrun` artifact may need an
explicit origin declaration; a directly presented generated artifact does not.

If an action fails because existing research-owned state is malformed or
legacy, report the exact failure and stop. A failed Record command does not
authorize Repair or direct registry editing.

During Review, report missing declarations, raw-path bypasses, unused inputs,
duplicate targets, cross-entry disagreement, changed bytes, remote-only
material, and origin boundaries that hide confirmed same-log producers. Do not
refresh bytes or choose an origin boundary without researcher authority.
