# Example Downstream Testing Document

This is an example `docs/testing.md` for a project that uses `astro-agents`.

Copy or adapt it into the downstream project's own `docs/testing.md`, then
replace the examples with that project's real commands, test suites, release
checks, and completion expectations.

## Project Checks

List each check with the changes that require it and when it must run.

Examples:

```bash
pytest
```

```bash
git diff --check
```

If detailed guidance becomes long, move it into directly linked references and
use a change-to-check table with the trigger, required check, milestone, and
reference. Separate mandatory, conditional, and optional checks so readers
need not load unrelated workflows.

## Completion Standard

- A failed required check blocks the change it covers.
- Record required checks that failed or were not run.
- Repeat checks only after relevant state changes or unresolved failures.
