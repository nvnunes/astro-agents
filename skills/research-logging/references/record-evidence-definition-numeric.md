# Numeric And Short Evidence

Author the EID definition beside its code span. New and existing items use the
same format; a new item's span may be empty. Then `log evidence sync --id EID`.

```markdown
``<!-- eid:error source=metrics select=/error render=fixed:3 -->
``<!-- eid:rate source=metrics select=/rate form=percentage render=fixed:1 -->
``<!-- eid:range form=range unit=nm source=metrics select=/low select=/high render=fixed:2 -->
``<!-- eid:estimate form=plus_minus source=metrics select=/mean render=fixed:2;
source=uncertainty select=/error render=fixed:1 -->
```

The default is one scalar. Supported forms are scalar (one value), percentage
(one retained proportion), Boolean (one value), range and plus_minus (two),
interval (three), and tuple (2–8). Several selected records can supply the
operands of a compound form; declared filters and observed cardinality determine
those operands, never implicit joins.

- `render=integer`, `grouped_integer`, `fixed:N`, `scientific:N`, or
  `significant:N` declares numeric spelling. N is decimal places for fixed,
  significant figures for scientific/significant.
- `parse=decimal` or `integer` explicitly parses numeric source text.
- `scale=DECIMAL` applies a researcher-authorized scale.
- `magnitude=true` uses absolute magnitude; `sign=always` emits an explicit sign.
- `unit=UNIT` belongs to the whole compound presentation. Quote values with
  spaces using shell quoting.
- Render, parse, scale, magnitude, and sign may be uniform within a source or
  repeated once per selected operand for heterogeneous formatting.
- `render=boolean:true_false` (or yes_no, pass_fail) presents a
  native Boolean; Boolean source text requires `parse=boolean`.

`reproduction_tolerance=ABSOLUTE_DECIMAL` optionally declares a researcher-
approved numeric tolerance for this evidence. The artifact separately selects
`reproduction_comparison: evidence` through `log data update`. The tolerance
does not relax Markdown agreement, accept retained-byte drift, or select a
whole artifact's reproduction policy.

Keep connective prose outside code spans. Unsupported computation belongs in a
recorded script, not a new comment language or generic expression.
