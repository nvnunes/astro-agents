# Input Registry Instructions

Use this file when registering or changing a material input, using a `<name>`
input token, or declaring where a Provenance chain stops. Use the public
`<skill>/scripts/log data` actions for ordinary authoring. Never create or edit
`data.json` directly outside an explicitly authorized Repair.

`data.json` is the complete material-input registry. It contains all and only
file and directory resources consumed by recorded commands or mechanical
evidence, plus an exact directly presented artifact only when `origin: true`
is needed to stop its Provenance chain. It is not a general artifact inventory,
evidence declaration, or producer registry.

Omit `data.json` when the entry has no command or evidence inputs and no direct
artifact origin. A present file uses schema `research-log-data/v3` and contains
a non-empty `inputs` array. Split documents at one entry root share that file.
Do not create a parent-entry or log-level registry and do not inherit or merge
another file.

Each input has:

- `name`: a unique entry-scoped ASCII token name; `log`, `project`, `theme`,
  and numeric entry-family names such as `e004` are reserved;
- `kind`: `file` or `directory`;
- `location`: a normalized local path from the entry root or an absolute local
  path;
- `fingerprint`: SHA-256 for a local file, `directory-sha256-v1` for a small
  byte-complete directory, `identity-files-sha256-v1` for a managed directory
  with explicit authoritative identity files,
  `identity-patterns-sha256-v1` for a managed directory with bounded exact and
  wildcard file selectors; and
- `origin`: a required boolean. `true` explicitly stops the Provenance chain at
  this artifact after its current byte fingerprint is verified. `false`
  requires a validated producer and recursive upstream lineage.

Use exact `<name>` arguments for file inputs. Use `<directory-name>` with an
`input-directory` role to consume either every byte-complete descendant or one
managed logical aggregate according to its fingerprint algorithm. Use
`<directory-name>/member` to consume one exact member. Raw relative paths,
absolute paths, and URIs are invalid for command inputs and evidence sources
even when they match a declared location. An evidence source must resolve to
one local regular file, so use `<name>` for a file or
`<directory-name>/member` for one exact directory member; a bare directory
token is not evidence.

Register a producerless local file or directory as an explicit origin:

```bash
<skill>/scripts/log data add-origin --path <log> --entry <entry-id> \
  development_set /data/project/development.csv
<skill>/scripts/log data add-origin --path <log> --entry <entry-id> \
  reference_cases data/reference-cases
```

The CLI infers file versus directory, normalizes the target, records its
current fingerprint, and asserts that no confirmed producer in the same log is
hidden by the origin boundary.

For a large managed directory, declare the bounded files that authoritatively
identify the logical resource. Do not use this form merely to avoid hashing;
the named files must change whenever the scientifically relevant resource
identity changes:

```bash
<skill>/scripts/log data add-origin --path <log> --entry <entry-id> \
  build_root /data/builds/v3 \
  --identity build.h5 --identity build.yaml
```

`identity-files-sha256-v1` accepts 1–64 exact normalized relative file paths.
It hashes only those non-symlink regular files and never traverses the declared
directory. Its aggregate digest covers each relative path and file SHA-256.
Undeclared descendants are deliberately outside the bytewise identity. Use it
for a producer-owned build, dataset, cache, or archive whose root control files
or manifests are the authoritative logical identity. Continue using
`directory-sha256-v1` when complete descendant membership is itself the
contract.

When a managed producer owns a small variable family of root files, use bounded
identity patterns:

```bash
<skill>/scripts/log data add-origin --path <log> --entry <entry-id> \
  build_root /data/builds/v3 \
  --identity build.h5 --identity build.yaml --identity build.log \
  --identity "maps-*.h5"
```

`identity-patterns-sha256-v1` accepts 1–64 normalized selectors and resolves at
most 64 unique regular files. Exact selectors may name nested files. Wildcards
(`*`, `?`, and character classes) are allowed only in the final path component;
an exact selector must resolve to one regular file, while a wildcard selector
may resolve to zero files. The selector set as a whole must resolve to at least
one file. Recursive `**`, wildcard parent directories, symlinks, and overlapping
selectors are invalid. Validation re-expands every selector, so an added,
removed, or renamed matching file changes the aggregate fingerprint. The
fingerprint covers the selectors, sorted matched paths, and each file's SHA-256.
It scans each distinct wildcard parent once, examines at most 100,000 immediate
entries in that parent, and never traverses undeclared descendants.

Register generated material only after its producing `pyrun` command succeeds:

```bash
<skill>/scripts/log data add-generated --path <log> --entry <entry-id> \
  generated_samples data/generated-samples.csv
```

The action requires one current confirmed producer in the same maintained log
and an exact match to the target's current bytes. Production in another log is
an origin boundary rather than same-log generated lineage.

Fingerprint drift is a validation failure and never updates the registry as a
side effect of command execution. After intentionally changing or replacing an
input, refresh it explicitly:

```bash
<skill>/scripts/log data refresh --path <log> --entry <entry-id> \
  development_set
```

Use `log data update` to change a target, explicitly change its origin or
generated classification, replace an origin directory's repeated `--identity`
selectors, or select `--byte-complete`. Omitted properties remain unchanged.
Use `log data rename` after updating every recorded-command token; it renames
same-entry evidence source tokens atomically and reports producer commands that
must be rerun. Use `log data remove` only after removing command and evidence
use. Removing the final item removes `data.json`. Use `log data list` for a
bounded semantic inventory; it does not expose fingerprints or registry
structure. Every mutation accepts `--dry-run`.

A generated output belongs in `data.json` when a later recorded command or an
evidence record consumes it. Set `origin: false` so it must trace to its unique
earlier producer. An output of a historical non-`pyrun` workflow may instead
be marked `origin: true` only when the researcher deliberately chooses to stop
validated lineage at those current bytes. Outputs consumed by neither surface
do not belong in the registry, except for an exact directly presented
non-`pyrun` artifact declared as an origin. Images, command logs, and scripts
otherwise follow the same rule: register them only when they are material
inputs to a command or evidence record.

Storage location does not decide origin status. An origin may be inside the
entry, and a generated intermediate may be elsewhere. Base the boundary on the
intended Provenance chain.

During review, report missing input declarations, raw-path token bypasses,
unused items, duplicate targets, conflicting declarations for one target,
fingerprint drift, remote-only material, and an origin boundary that hides a
confirmed `pyrun` producer. Do not refresh fingerprints or decide origin status
without researcher authority.
