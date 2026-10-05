# reconcile/

Written only by `.github/workflows/reconcile.yml` (jevaudit.audit), never by the listener.

- `reconcile_<from>_<to>.json` / `.md` — one report per run (counts, missing fills, extra fills,
  input_hash mismatches, duplicates, blocks without node majority, unanswered ranges).
- `cursor.json` — last block reconciled by the daily run; manual `--from/--to` runs do not move it.
