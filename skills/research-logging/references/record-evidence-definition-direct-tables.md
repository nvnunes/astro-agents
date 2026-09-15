# Direct Markdown Tables

Use one evidence record when one retained artifact already supplies the table's
row-and-column shape. Author the header and alignment row; sync fills or updates
the body while preserving those authored rows and surrounding text.

```markdown
<!-- eid:cases source=metrics identity=/name;
column=/error parse=decimal render=fixed:3;
column=/name render=text -->
| Error | Case |
| ---: | :--- |
```

The source clause may use path, identity, and where selection fields described in
`references/record-evidence-definition-sources.md`. Each `column=POINTER`
clause binds one Markdown column in the same order. This permits a subset of
source columns and column reordering; the Markdown header owns displayed names.
Columns support text, scalar numeric rendering/parse/scale/magnitude/sign,
`render=percentage:N`, and `render=boolean:STYLE`.
Do not repeat `select` separately when columns provide the selections.

For a nested array, use `path=POINTER` to its row container and bind each
column with its explicit field/index pointer. Sync rejects shape mismatch.

There are no joins, literal or derived columns, row permutations beyond explicit
source selection, or compound cells. A summary table is ordinary Markdown with
separate evidence cells. For a joined or derived table record a script producing
a new presentation-ready artifact, then sync this direct form. Do not alter
presented research to fit a table shortcut.
