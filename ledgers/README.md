# ledgers/

Listener output goes here (`chain_listener_YYYYMMDD.jsonl.gz`, plus state/status/events).
The daily `reconcile` workflow reads these files and writes results to `reconcile/` only.
The original 24h run stays in `raw/ledger/` (unchanged).
