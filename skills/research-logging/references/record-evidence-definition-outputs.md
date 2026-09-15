# Verbatim Retained Output

Place the source and explicit bounds in the EID comment immediately before a
`text` fence. New output uses an empty fence; existing output uses the same
format. Sync replaces only the payload.

````markdown
<!-- eid:status source=run-log line=2 chars=2:8 -->
```text
```
````

`line=N` selects one whole source line. Optional `chars=START:END` selects
one-based inclusive Unicode character positions on that line.
`lines=START:END` selects an inclusive line range and cannot be combined with
chars or line. Bounds must exist. UTF-8 content is retained verbatim with source
line endings normalized to LF. There is no substring-search locator,
transformation, trimming, or inference from the currently displayed payload.

Run `log evidence compare --id EID` to see before/after, then
`log evidence sync --id EID` to accept. For complete inline diffs use a
`diff` fence and one whole-artifact source instead of excerpt bounds.
