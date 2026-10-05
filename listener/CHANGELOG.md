# listener CHANGELOG

## v3 — 2026-10-05 (listener.py sha256 `ae9aba16c8519c491b6bcac1bcd8e076c5f9cedaf905619ae47526448048062e`)

Why: in the 24h run (10-02 10:46 → 10-03 12:15 UTC) blocks 94834428, 94840332, 94862944,
94875380, 94875386 were lost (61 tracked fills). Each time the node reported head=m, eth_getLogs
over [m-1, m] returned logs for m-1 only, no error, and v2 moved the cursor to m+1.
Evidence: openship-fork `raw/repull/gl/`, ledger records of block m-1 carry `head = m`.

Changes (listener.py only; gate code untouched):
1. `CONFIRM_DEPTH` env var, default 10. Only blocks `last_processed+1 .. head-CONFIRM_DEPTH` are read.
   If `last_processed >= head-CONFIRM_DEPTH` the round is skipped (cursor untouched).
2. Explicit per-block state (`BlockState`):
   - `DATA_PRESENT` – logs returned (by primary, or by a second node when primary had none) → written, cursor may pass.
   - `CONFIRMED_EMPTY` – primary and a different second node both answered "no logs" → cursor may pass.
   - `UNAVAILABLE` – no second node answered → cursor stops at the block before; retried next round.
   A failed call is `Fetch(ok=False, logs=None)`; it is never represented as an empty list.
3. Checkpoint (`chain_listener_state.json`) gains `last_processed`, `last_confirmed`, `pending_tip`,
   `confirm_depth`, `updated_at`, `block_states` (counters). `next_block` kept (= last_processed+1).
   v2 state files are migrated on start (`last_processed = next_block-1`).
4. New events: `confirmed_empty`, `primary_empty_secondary_data`, `unavailable`.
5. Default `RPCS` adds a third node: `https://polygon-bor-rpc.publicnode.com`.
6. Status file adds the checkpoint fields and `block_states`.
7. `test_listener.py` (stdlib, mock RPC, no network): head=256 with logs only up to 255 →
   asserts `last_processed == 246` (depth 10) / `== 255` (depth 0), 256 not in states or ledger,
   `pending_tip == 256`, 256 = `UNAVAILABLE` when no second node answers.
   Run: `python -m unittest test_listener -v` → 7 tests OK.

Cost of the fix: records arrive ~CONFIRM_DEPTH × 2 s ≈ 20 s later than v2.
Known limit: a node returning SOME but not all logs of a block is not caught here → independent reconcile.
Live smoke test 2026-10-05 06:33 UTC, TRACK=all, 29 s: 20 blocks DATA_PRESENT, 1,779 fills, 0 errors.

## v2 — 2026-10-02 (sha256 `68e29fd1e5973ded6efd7c8eab14501104450f81393521c9d273d816f7349b36`)
Version that ran the 24h test. Unchanged copy committed first (commit `1fc98da`), original in `raw/listener/`.
