# Retained Supporting Material

Use retention for material intentionally kept outside the provenance chain for
presented evidence. It explains why the material remains without inventing a
consumer, producer, or evidence relationship.

Required tooling:

```bash
"$LOG_TOOL" retention add --path "$LOG" --entry e001 \
  --id pilot-timings \
  --reason "Retained to estimate runtime for future simulations." \
  data/pilot-timings.csv
```

Research commands: none are added by retention. Preserve any real producer
record; retaining a file neither runs nor reproduces it, establishes evidence,
nor replaces required producer support.
