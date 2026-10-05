"""Independent audit of an on-chain fill ledger (Polymarket OrderFilled on Polygon).

Two checks, independent from the listener that wrote the ledger:

verify_ledger_integrity(records, known_code_hashes)
    Per record: output_hash recomputed from the record itself; code_hash must be registered;
    input_hash must be a 64-hex sha256 (its content is checked against the chain in reconcile).
    Duplicate (tx, log_index) pairs are failures.

reconcile_with_chain(records, rpcs, lo, hi, wallets=None)
    Re-reads every block lo..hi from the chain and compares fill by fill:
      * primary pass: all blocks from the first node that answers (per chunk, failover);
      * every block where the primary disagrees with the ledger, or returned nothing, is re-asked
        on every other node; the block's truth is the answer at least 2 nodes agree on;
      * a random sample of blocks is asked on all nodes to measure node agreement;
      * input_hash of every matched fill is recomputed from the raw chain log.
    A node that does not answer is recorded as "no answer", never as "no logs".

Ledger record format = chain listener v2/v3 (fields block, tx, log_index, wallet, input_hash,
output_hash, code_hash). Stdlib only.
"""
import concurrent.futures as cf
import hashlib
import json
import random
import time
import urllib.request

TOPIC = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"  # OrderFilled
EXCHANGES = frozenset({
    "0xe111180000d2663c0091e4f400237545b87b996b",
    "0xe2222d279d744050d28e00520010520000310f59",
    "0xe3333700ca9d93003f00f0f71f8515005f6c00aa",
})
DEFAULT_RPCS = (
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
    "https://polygon-bor-rpc.publicnode.com",
)
_UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
_HEX64 = set("0123456789abcdef")


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(o):
    return hashlib.sha256(canon(o).encode()).hexdigest()


def _is_sha256(h):
    return isinstance(h, str) and len(h) == 64 and set(h) <= _HEX64


# ---- 1) integrity --------------------------------------------------------------------------
def verify_ledger_integrity(records, known_code_hashes=None, max_failures=200):
    """Check the three fingerprints of every record. known_code_hashes empty -> every record fails
    the code_hash check (fail-closed)."""
    known = set(known_code_hashes or ())
    fails, n_fail, seen, by_code = [], 0, set(), {}
    for i, r in enumerate(records):
        bad = []
        body = {k: v for k, v in r.items() if k != "output_hash"}
        if not _is_sha256(r.get("output_hash")):
            bad.append("output_hash missing/malformed")
        elif sha(body) != r["output_hash"]:
            bad.append("output_hash mismatch")
        if not _is_sha256(r.get("input_hash")):
            bad.append("input_hash missing/malformed")
        ch = r.get("code_hash")
        by_code[ch] = by_code.get(ch, 0) + 1
        if ch not in known:
            bad.append("code_hash not registered")
        key = (r.get("tx"), r.get("log_index"))
        if key in seen:
            bad.append("duplicate tx/log_index")
        seen.add(key)
        if bad:
            n_fail += 1
            if len(fails) < max_failures:
                fails.append({"i": i, "block": r.get("block"), "tx": r.get("tx"), "log_index": r.get("log_index"), "reasons": bad})
    return {"ok": n_fail == 0, "n_records": len(records), "n_failed": n_fail,
            "by_code_hash": by_code, "failures": fails}


# ---- RPC layer -----------------------------------------------------------------------------
class Answer:
    """One node's answer for a block range. ok=False -> logs is None (no answer, NOT 'no logs')."""
    __slots__ = ("ok", "logs", "url", "err")

    def __init__(self, ok, logs, url, err=None):
        self.ok, self.logs, self.url, self.err = ok, logs, url, err


def _post(url, method, params, timeout=40):
    req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(), headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read())
    if res.get("result") is None:
        raise RuntimeError(f"rpc error: {str(res.get('error'))[:200]}")
    return res["result"]


def get_logs(url, a, b, retries=2):
    err = None
    for t in range(retries + 1):
        try:
            res = _post(url, "eth_getLogs", [{"fromBlock": hex(a), "toBlock": hex(b), "topics": [TOPIC]}])
            if not isinstance(res, list):
                raise RuntimeError(f"non-list result {type(res).__name__}")
            out = [l for l in res if l.get("address", "").lower() in EXCHANGES]
            bad = sorted({int(l["blockNumber"], 16) for l in out} - set(range(a, b + 1)))
            if bad:  # node answered with other blocks' logs (the 2026-10-03 incident) -> void, no retry
                return Answer(False, None, url, f"out-of-range logs {bad[:5]} for {a}-{b}")
            return Answer(True, out, url)
        except Exception as e:
            err = repr(e)[:300]
            time.sleep(1 + t)
    return Answer(False, None, url, err)


def _key(l):
    return (l["transactionHash"].lower(), int(l["logIndex"], 16))


def _maker(l):
    return ("0x" + l["topics"][2][-40:]).lower() if len(l.get("topics", [])) >= 4 else None


# ---- 2) reconcile --------------------------------------------------------------------------
def reconcile_with_chain(records, rpcs=DEFAULT_RPCS, lo=None, hi=None, wallets=None, step=20,
                         workers=4, sample_blocks=30, seed=0, max_list=5000, progress=None):
    """Compare ledger `records` with the chain for blocks lo..hi (default: min..max ledger block).
    wallets: set of tracked maker addresses (lower-case); None = ledger tracks all fills."""
    rpcs = list(rpcs)
    if len(rpcs) < 2:
        raise ValueError("need at least 2 independent RPCs")
    wl = {w.lower() for w in wallets} if wallets is not None else None
    tracked = (lambda l: _maker(l) in wl) if wl is not None else (lambda l: _maker(l) is not None)
    blocks_in_ledger = [r["block"] for r in records]
    lo = min(blocks_in_ledger) if lo is None else lo
    hi = max(blocks_in_ledger) if hi is None else hi
    led = {}
    for r in records:
        if lo <= r["block"] <= hi:
            led.setdefault(r["block"], {})[(r["tx"].lower(), r["log_index"])] = r
    t0 = time.time()

    # primary pass
    chunks = [(a, min(a + step - 1, hi)) for a in range(lo, hi + 1, step)]

    def pull(ch):
        a, b = ch
        start = (a // step) % len(rpcs)
        for k in range(len(rpcs)):
            ans = get_logs(rpcs[(start + k) % len(rpcs)], a, b)
            if ans.ok:
                return ch, ans
        return ch, ans

    primary, unanswered_chunks = {}, []
    with cf.ThreadPoolExecutor(workers) as ex:
        for n, (ch, ans) in enumerate(ex.map(pull, chunks)):
            if not ans.ok:
                unanswered_chunks.append({"from": ch[0], "to": ch[1], "err": ans.err})
                continue
            by = {bn: [] for bn in range(ch[0], ch[1] + 1)}
            for l in ans.logs:
                by[int(l["blockNumber"], 16)].append(l)
            for bn, ls in by.items():
                primary[bn] = (ans.url, ls)
            if progress and n % 200 == 0:
                progress(f"primary {n}/{len(chunks)} chunks, {time.time() - t0:.0f}s")

    # decide which blocks need a second and third opinion
    def tracked_keys(ls):
        return {_key(l) for l in ls if tracked(l)}

    suspect = [bn for bn, (_, ls) in primary.items() if not ls or tracked_keys(ls) != set(led.get(bn, {}))]
    rnd = random.Random(seed)
    answered = sorted(primary)
    sample = sorted(rnd.sample(answered, min(sample_blocks, len(answered)))) if answered else []

    def all_nodes(bn):
        return bn, [get_logs(u, bn, bn) for u in rpcs]

    multi = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        for bn, answers in ex.map(all_nodes, sorted(set(suspect) | set(sample))):
            multi[bn] = answers

    def truth(bn):
        """Majority answer among nodes that answered: (status, logs). status: AGREED / NO_MAJORITY / NO_ANSWER."""
        answers = [a for a in multi[bn] if a.ok]
        if len(answers) < 2:
            return "NO_ANSWER", None, answers
        sigs = {}
        for a in answers:
            sigs.setdefault(frozenset(_key(l) for l in a.logs), []).append(a)
        best = max(sigs.values(), key=len)
        if len(best) >= 2:
            return "AGREED", best[0].logs, answers
        return "NO_MAJORITY", None, answers

    sample_agree = 0
    for bn in sample:
        st, _, ans = truth(bn)
        if st == "AGREED" and len({frozenset(_key(l) for l in a.logs) for a in ans}) == 1:
            sample_agree += 1

    missing, extra, hash_bad, unresolved, empty_confirmed, primary_wrong = [], [], [], [], [], []
    n_chain_all = n_chain_tracked = n_matched = 0
    missing_blocks = {}
    for bn in range(lo, hi + 1):
        if bn in multi:
            st, logs, ans = truth(bn)
            if st != "AGREED":
                unresolved.append({"block": bn, "status": st, "answers": [{"url": a.url, "ok": a.ok, "n": None if a.logs is None else len(a.logs), "err": a.err} for a in multi[bn]]})
                continue
            if bn in primary and frozenset(_key(l) for l in primary[bn][1]) != frozenset(_key(l) for l in logs):
                primary_wrong.append({"block": bn, "primary": primary[bn][0], "primary_n": len(primary[bn][1]), "majority_n": len(logs)})
            if not logs:
                empty_confirmed.append(bn)
        elif bn in primary:
            logs = primary[bn][1]
        else:
            continue  # chunk unanswered, reported above
        n_chain_all += len(logs)
        chain_t = {_key(l): l for l in logs if tracked(l)}
        n_chain_tracked += len(chain_t)
        lb = led.get(bn, {})
        for k, l in chain_t.items():
            r = lb.get(k)
            if r is None:
                if len(missing) < max_list:
                    missing.append({"block": bn, "tx": k[0], "log_index": k[1], "wallet": _maker(l)})
                missing_blocks[bn] = missing_blocks.get(bn, 0) + 1
            else:
                n_matched += 1
                if r.get("input_hash") != sha(l):
                    hash_bad.append({"block": bn, "tx": k[0], "log_index": k[1]})
        for k in lb:
            if k not in chain_t:
                extra.append({"block": bn, "tx": k[0], "log_index": k[1]})

    n_ledger = sum(len(v) for v in led.values())
    n_missing = sum(missing_blocks.values())
    ok = not (n_missing or extra or hash_bad or unresolved or unanswered_chunks)
    return {
        "ok": ok,
        "range": {"from": lo, "to": hi, "blocks": hi - lo + 1},
        "rpcs": rpcs,
        "counts": {"ledger_fills": n_ledger, "chain_fills_all": n_chain_all, "chain_fills_tracked": n_chain_tracked,
                   "matched": n_matched, "missing_in_ledger": n_missing, "extra_in_ledger": len(extra),
                   "input_hash_mismatch": len(hash_bad)},
        "missing_blocks": [{"block": b, "missing_fills": n, "ledger_fills": len(led.get(b, {}))} for b, n in sorted(missing_blocks.items())],
        "missing_fills": missing,
        "extra_fills": extra[:max_list],
        "input_hash_mismatch": hash_bad[:max_list],
        "empty_blocks_confirmed": empty_confirmed,
        "unresolved_blocks": unresolved,
        "unanswered_chunks": unanswered_chunks,
        "primary_node_wrong": primary_wrong,
        "node_agreement_sample": {"blocks": len(sample), "all_nodes_identical": sample_agree},
        "multi_node_checked_blocks": len(multi),
        "elapsed_s": round(time.time() - t0, 1),
    }


# ---- 3) verdicts (added on test/capability-verdicts for the capability test) ----------------
def expected_partitions(start_ts, end_ts, prefix="chain_listener_"):
    """UTC day partitions a listener running start_ts..end_ts must have written (one file per day)."""
    out, d = [], int(start_ts) // 86400
    while d <= int(end_ts) // 86400:
        out.append(prefix + time.strftime("%Y%m%d", time.gmtime(d * 86400)))
        d += 1
    return out


def check_partitions(files, start_ts, end_ts):
    """Return the expected day partitions that are absent from `files` (basenames, any .jsonl[.gz])."""
    import os
    have = {os.path.basename(f).split(".")[0] for f in files}
    return [p for p in expected_partitions(start_ts, end_ts) if p not in have]


def verdicts(rep, integ, records, missing_partitions=(), ext=".jsonl.gz"):
    """Turn a reconcile report into explicit finding codes. Empty list == PASS.
    TRAILING_GAP: the ledger stops before the checked upper bound and the chain has tracked fills there.
    MISSING_BLOCK: a block with tracked fills on chain and 0 in the ledger (before the ledger's last block).
    MISSING_FROM_LEDGER: individual fills missing from a block the ledger does have.
    """
    lo, hi = rep["range"]["from"], rep["range"]["to"]
    in_rng = [r["block"] for r in records if lo <= r["block"] <= hi]
    led_max = max(in_rng) if in_rng else lo - 1
    out = []
    for p in missing_partitions:
        out.append({"code": "MISSING_DAY_PARTITION", "file": p + ext})
    tail = [m for m in rep["missing_blocks"] if m["block"] > led_max]
    if tail:
        out.append({"code": "TRAILING_GAP", "from": led_max + 1, "to": hi, "ledger_last_block": led_max,
                    "blocks_with_fills": len(tail), "fills": sum(m["missing_fills"] for m in tail)})
    for m in rep["missing_blocks"]:
        if m["block"] > led_max:
            continue
        if m["ledger_fills"] == 0:
            out.append({"code": "MISSING_BLOCK", "block": m["block"], "fills": m["missing_fills"]})
    part = {m["block"] for m in rep["missing_blocks"] if m["ledger_fills"] > 0 and m["block"] <= led_max}
    mf = [f for f in rep["missing_fills"] if f["block"] in part]
    if mf:
        out.append({"code": "MISSING_FROM_LEDGER", "n": sum(m["missing_fills"] for m in rep["missing_blocks"] if m["block"] in part),
                    "fills": mf[:50]})
    if rep["extra_fills"]:
        out.append({"code": "EXTRA_IN_LEDGER", "n": rep["counts"]["extra_in_ledger"], "fills": rep["extra_fills"][:50]})
    if rep["input_hash_mismatch"]:
        out.append({"code": "INPUT_HASH_MISMATCH", "n": rep["counts"]["input_hash_mismatch"]})
    if not integ.get("ok", True):
        out.append({"code": "INTEGRITY_FAIL", "n": integ["n_failed"]})
    for u in rep["unresolved_blocks"]:
        out.append({"code": "UNAVAILABLE", "block": u["block"], "status": u["status"]})
    for u in rep["unanswered_chunks"]:
        out.append({"code": "UNAVAILABLE", "from": u["from"], "to": u["to"], "status": "NO_ANSWER"})
    return out
