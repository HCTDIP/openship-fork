# listener CHANGELOG

## v3 — 2026-10-05 (listener.py sha256 `0bd0618033e0ba07147406c3a4a63fff89999ca8bf47544aaeeb0bd88bd2eb99`)

(intermediate commit 42f50af had `ae9aba16…` without the out-of-range guard; superseded.)

Why: in the 24h run (10-02 10:46 → 10-03 12:15 UTC) blocks 94834428, 94840332, 94862944,
94875380, 94875386 were lost (61 tracked fills). Each time: one poll read up to m-1 (head=m-1);
the next poll asked eth_getLogs for [m, m] with head=m, and the node answered with block m-1's logs
(no error). v2 wrote them again and moved the cursor to m+1 -> block m lost, block m-1 written twice.
Evidence: the 134 duplicate ledger records are all in blocks m-1 (22+35+12+49+16), the second copy
carries `head = m` and a detect_ts ~1 s after the first; chain repull in openship-fork `raw/repull/gl/`.

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
4. An answer with any log outside the requested block range is void (`Fetch(ok=False)`, event
   `out_of_range_logs`). This is the exact incident behaviour.
5. New events: `confirmed_empty`, `primary_empty_secondary_data`, `unavailable`, `out_of_range_logs`.
6. Default `RPCS` adds a third node: `https://polygon-bor-rpc.publicnode.com`.
7. Status file adds the checkpoint fields and `block_states`.
8. `test_listener.py` (stdlib, mock RPC, no network): head=256 with logs only up to 255 →
   asserts `last_processed == 246` (depth 10) / `== 255` (depth 0), 256 not in states or ledger,
   `pending_tip == 256`, 256 = `UNAVAILABLE` when no second node answers.
   Plus the exact incident (asked [256,256], node returns 255's logs → 255 written once, 256 never).
   Run: `python -m unittest test_listener -v` → 8 tests OK.

Cost of the fix: records arrive ~CONFIRM_DEPTH × 2 s ≈ 20 s later than v2.
Known limit: a node returning SOME but not all logs of a block is not caught here → independent reconcile.
Live smoke test 2026-10-05 06:33 UTC, TRACK=all, 29 s: 20 blocks DATA_PRESENT, 1,779 fills, 0 errors.

## v2 — 2026-10-02 (sha256 `68e29fd1e5973ded6efd7c8eab14501104450f81393521c9d273d816f7349b36`)
Version that ran the 24h test. Unchanged copy committed first (commit `1fc98da`), original in `raw/listener/`.
