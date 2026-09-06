# Summary Reproduction Navigation

Use this file when initializing or preserving the maintained summary's stable
link to its generated reproduction report.

Place this exact line after the Validation link and its blank line, followed by
one blank line:

```md
Reproduction: [latest report](<log>/reproduction.md)
```

The link contains no date, outcome, failure count, currentness claim, or
contract version. It is the summary's complete reproduction surface and has no
matching item in `## Contents`.

Record initialization installs the link together with empty
`reproduction/results.json` and a generated `reproduction.md` that states no
run has completed. Record, Replace, Update Summary, Repair, and Reorganize
preserve the line. Reproduce owns the generated machine and human surfaces but
never changes this summary navigation.
