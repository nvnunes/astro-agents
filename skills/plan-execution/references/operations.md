# Operations

## Before Expensive Work

Cheaply check current preconditions and a representative operation, including
its entrypoint and output/persistence path when relevant. Planning-time
observations may be stale; reuse evidence only while applicable. Check assumptions that could change the
approach or prevent its result, and resolve failures before investing further.

## Running Operations

For commands that may outlast a tool call, use the tool's saved-result and
recovery interfaces when available. Retain its execution handle and result ID;
capture output to files at launch when the tool does not retain queryable results.
Follow the handle to completion and retain the exit status. Do not rely on the
final tool response to preserve the result or duplicate retrievable payloads.

Use existing status, wait, or recovery interfaces before retrying an uncertain
launch; a missing output file does not establish that nothing ran.

Before stopping or restarting an operation, check its latest status and saved
results. It may already have completed useful work; use that evidence to avoid
duplicating work or losing progress. Report uncertainty when state is unknown.

## Repeated Inspection

Inspect named paths and bounded results. Reuse inventories only while source
state remains applicable. Use existing timing, status, or logs to investigate
unexpected slowness before repeating work. Keep the investigation scoped to
the delay rather than building a benchmarking project.
