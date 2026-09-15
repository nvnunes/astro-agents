# Evidence Source Selection

Use these fields in the EID comment, then `log evidence sync --id EID`.
Each source clause starts with `source=NAME` or `source=NAME/member`.
Repeat `select=POINTER` in the desired order.

Pointers use JSON Pointer escaping (`~0` for ~, `~1` for /), zero-based numeric
indexes, `/*` for every element, and `/[START:END]` for a half-open slice.
Use `path=POINTER` when selection begins beneath a nested source container.
Supported sources include CSV/TSV, JSON/JSONL, NPZ, and HDF5; selection must be
explicit rather than guessing a file from stored metadata.

```markdown
``<!-- eid:selected source=metrics path=/cases select=/error
identity=/name where=/name:eq:string:baseline parse=decimal render=fixed:2 -->
```

A comment may span source lines. `identity=POINTER` may be repeated for stable
record identity. `where=POINTER:eq:TYPE:VALUE` filters equality;
`where=POINTER:in:TYPE:VALUE,VALUE` filters a declared set. TYPE is string,
integer, decimal, boolean, or null. Percent-encode comma-containing string
members of an in-set. Repeated conditions are a conjunction, not inferred joins.
For numeric CSV comparison and rendering use `parse=decimal` or
`parse=integer`. Sync derives matches, selected items, identities, shape, and
current source fingerprints; do not author `expect` or fingerprint fields.

Multiple source clauses separated by semicolons are supported only for the
closed compound numeric forms. A direct table has one source clause.
Use a script when the requested selection requires an unsupported calculation.
