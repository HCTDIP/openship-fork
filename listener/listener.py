"""Polymarket chain listener v2 — RECORD ONLY (never trades, never calls Polymarket APIs).

Only listens to OrderFilled logs of the 3 Polymarket exchange contracts on Polygon,
keeps fills whose maker is a tracked wallet (or all fills if TRACK=all), and appends
one JSON line per fill to  $LEDGER_DIR/chain_listener_<YYYYMMDD>.jsonl.gz  (UTC date;
gzip stream, sync-flushed + fsynced after every poll, so a crash loses at most one poll;
~2M fills / 72h -> ~300 MB gz vs ~1.5 GB raw). Set GZIP=0 for plain .jsonl.

Each record carries 3 fingerprints:
  input_hash  = sha256 of the raw RPC log (canonical JSON)
  output_hash = sha256 of this record (canonical JSON, without output_hash)
  code_hash   = sha256 of this listener.py file

Latency = detect_ts (local clock when getLogs returned) - block_ts (chain block timestamp).
Block timestamp comes from the log's `blockTimestamp` field when the RPC returns it;
otherwise one cached eth_getBlockByNumber call per block (the only extra call).

Tip-race fix (v3, 2026-10-05): the 24h run lost 5 blocks because the node said head=m but
returned no logs for m yet; the cursor moved past m. Now:
  * only blocks <= head - CONFIRM_DEPTH (default 10) are read;
  * every block in a range gets an explicit state:
      DATA_PRESENT     logs returned -> written, cursor may pass it
      CONFIRMED_EMPTY  primary node AND a second, different node both answered "no logs"
      UNAVAILABLE      the second opinion could not be obtained -> cursor stops BEFORE it
    An answer containing logs outside the requested block range is void (Fetch ok=False):
    in the incident the node, asked for block m, returned block m-1's logs (m lost, m-1 doubled).
    A failed call is a Fetch(ok=False); it is never turned into an empty list.
  * checkpoint fields: last_processed / last_confirmed / pending_tip / confirm_depth / updated_at.
Known limit: a node that returns SOME but not all logs of a block is not detected here;
that is what the independent reconcile (jevaudit.audit) is for.

Stdlib only. Memory is flat (no growing caches). Restart-safe: progress is kept in
$LEDGER_DIR/chain_listener_state.json; the run stops DURATION_H hours after FIRST start.
"""
import gzip, hashlib, json, os, shutil, sys, time, urllib.request

TOPIC = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"  # OrderFilled
EXCHANGES = {
    "0xe111180000d2663c0091e4f400237545b87b996b",
    "0xe2222d279d744050d28e00520010520000310f59",
    "0xe3333700ca9d93003f00f0f71f8515005f6c00aa",
}
HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_DIR = os.environ.get("LEDGER_DIR", "/var/minis/shared/ledgers")
RPCS = [u.strip() for u in os.environ.get("RPCS", "https://polygon.drpc.org,https://1rpc.io/matic,https://polygon-bor-rpc.publicnode.com").split(",") if u.strip()]
DURATION_H = float(os.environ.get("DURATION_H", "72"))
TRACK = os.environ.get("TRACK", "wallets")  # wallets | all
WALLETS_FILE = os.environ.get("WALLETS_FILE", os.path.join(HERE, "wallets.json"))
POLL_S = float(os.environ.get("POLL_S", "0.5"))
GZ = os.environ.get("GZIP", "1") != "0"
MIN_FREE_MB = float(os.environ.get("MIN_FREE_MB", "30"))  # stop cleanly before the volume fills up
MAX_RANGE = int(os.environ.get("MAX_RANGE", "30"))  # blocks per getLogs
CONFIRM_DEPTH = int(os.environ.get("CONFIRM_DEPTH", "10"))  # never read the newest N blocks
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}

CODE_HASH = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()
STATE = os.path.join(LEDGER_DIR, "chain_listener_state.json")
STATUS = os.path.join(LEDGER_DIR, "chain_listener_status.json")
EVENTS = os.path.join(LEDGER_DIR, "chain_listener_events.jsonl")


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(o):
    return hashlib.sha256(canon(o).encode()).hexdigest()


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg, flush=True)


def event(ev, **kw):
    with open(EVENTS, "a") as f:
        f.write(canon({"ev": ev, "ts": time.time(), "code_hash": CODE_HASH, **kw}) + "\n")


def _post(url, method, params):
    """One JSON-RPC call to ONE node. Returns the `result`; raises on transport error or JSON-RPC error."""
    req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(), headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.loads(r.read())
    if "result" in res and res["result"] is not None:
        return res["result"]
    raise RuntimeError(f"rpc error: {res.get('error')}")


def rpc(method, params):
    err = None
    for u in RPCS:
        try:
            return _post(u, method, params), u
        except Exception as e:
            err = repr(e)
    raise RuntimeError(f"all rpc failed: {err}")


class BlockState:
    DATA_PRESENT = "DATA_PRESENT"
    UNAVAILABLE = "UNAVAILABLE"
    CONFIRMED_EMPTY = "CONFIRMED_EMPTY"


class Fetch:
    """Result of eth_getLogs on ONE node. ok=False means "no answer" -- logs is None, never []."""
    __slots__ = ("ok", "logs", "url", "err")

    def __init__(self, ok, logs, url, err=None):
        self.ok, self.logs, self.url, self.err = ok, logs, url, err


def get_logs_on(url, a, b):
    try:
        res = _post(url, "eth_getLogs", [{"fromBlock": hex(a), "toBlock": hex(b), "topics": [TOPIC]}])
        if not isinstance(res, list):
            return Fetch(False, None, url, f"non-list result {type(res).__name__}")
        out = [l for l in res if l.get("address", "").lower() in EXCHANGES]
        bad = sorted({int(l["blockNumber"], 16) for l in out} - set(range(a, b + 1)))
        if bad:  # 2026-10-03: asked for m, node returned m-1's logs -> the whole answer is void
            event("out_of_range_logs", url=RPCS.index(url) if url in RPCS else url, asked=[a, b], got=bad[:5])
            return Fetch(False, None, url, f"out-of-range logs {bad[:5]} for {a}-{b}")
        return Fetch(True, out, url)
    except Exception as e:
        return Fetch(False, None, url, repr(e)[:300])


def classify_range(a, b):
    """Read blocks a..b. Returns (primary_url, [(block, state, logs)]) for the settled prefix, plus the
    first UNAVAILABLE block (or None). Blocks after an UNAVAILABLE block are not looked at."""
    prim = None
    errs = []
    for u in RPCS:
        f = get_logs_on(u, a, b)
        if f.ok:
            prim = f
            break
        errs.append(f.err)
    if prim is None:
        raise RuntimeError(f"all rpc failed getLogs {a}-{b}: {errs[-1] if errs else 'no RPCS'}")
    by_block = {}
    for l in prim.logs:
        by_block.setdefault(int(l["blockNumber"], 16), []).append(l)
    out = []
    for bn in range(a, b + 1):
        if by_block.get(bn):
            out.append((bn, BlockState.DATA_PRESENT, by_block[bn]))
            continue
        # primary says "no logs" -> need a second, different node to agree
        sec = None
        for u in RPCS:
            if u == prim.url:
                continue
            f = get_logs_on(u, bn, bn)
            if f.ok:
                sec = f
                break
        if sec is None:
            return prim.url, out, (bn, "no second node answered")
        if sec.logs:
            event("primary_empty_secondary_data", block=bn, primary=RPCS.index(prim.url), secondary=RPCS.index(sec.url), n=len(sec.logs))
            out.append((bn, BlockState.DATA_PRESENT, sec.logs))
        else:
            event("confirmed_empty", block=bn, primary=RPCS.index(prim.url), secondary=RPCS.index(sec.url))
            out.append((bn, BlockState.CONFIRMED_EMPTY, []))
    return prim.url, out, None


_bts = {}  # tiny block->timestamp cache, cleared every range


def block_ts(l):
    if l.get("blockTimestamp"):
        return int(l["blockTimestamp"], 16), "log"
    bn = l["blockNumber"]
    if bn not in _bts:
        b, _ = rpc("eth_getBlockByNumber", [bn, False])
        _bts[bn] = int(b["timestamp"], 16)
    return _bts[bn], "getBlock"


def decode(l):
    d = l["data"][2:]
    v = [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]
    side = "BUY" if v[0] == 0 else "SELL"
    if side == "BUY":
        usdc, sh = v[2] / 1e6, v[3] / 1e6
    else:
        sh, usdc = v[2] / 1e6, v[3] / 1e6
    return {
        "wallet": "0x" + l["topics"][2][-40:], "taker": "0x" + l["topics"][3][-40:],
        "side": side, "token_id": str(v[1]), "shares": sh, "usdc": usdc,
        "price": round(usdc / sh, 6) if sh else None, "fee_raw": v[4] if len(v) > 4 else None,
        "exchange": l["address"].lower(), "block": int(l["blockNumber"], 16),
        "tx": l["transactionHash"], "log_index": int(l["logIndex"], 16),
    }


_ledger = {"day": None, "raw": None, "gz": None}


def write_ledger(det, recs):
    day = time.strftime("%Y%m%d", time.gmtime(det))
    if _ledger["day"] != day:
        close_ledger()
        fn = os.path.join(LEDGER_DIR, f"chain_listener_{day}.jsonl" + (".gz" if GZ else ""))
        _ledger["raw"] = open(fn, "ab")
        _ledger["gz"] = gzip.GzipFile(fileobj=_ledger["raw"], mode="ab", compresslevel=6) if GZ else None
        _ledger["day"] = day
    w = _ledger["gz"] or _ledger["raw"]
    w.write("".join(canon(r) + "\n" for r in recs).encode())
    w.flush()  # gzip: Z_SYNC_FLUSH
    _ledger["raw"].flush(); os.fsync(_ledger["raw"].fileno())


def close_ledger():
    if _ledger["gz"]:
        _ledger["gz"].close()
    if _ledger["raw"]:
        _ledger["raw"].close()
    _ledger.update(day=None, raw=None, gz=None)


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return None


def save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, STATE)


def migrate_state(st):
    """v2 state only had next_block. Add the v3 checkpoint fields without changing meaning."""
    if "last_processed" not in st:
        st["last_processed"] = st["next_block"] - 1
    st.setdefault("last_confirmed", None)
    st.setdefault("pending_tip", None)
    st.setdefault("confirm_depth", CONFIRM_DEPTH)
    st.setdefault("block_states", {BlockState.DATA_PRESENT: 0, BlockState.CONFIRMED_EMPTY: 0, BlockState.UNAVAILABLE: 0})
    return st


def poll_once(st, wallets):
    """One round. Returns a dict describing what happened (used by tests and the status file)."""
    head = int(rpc("eth_blockNumber", [])[0], 16)
    last_confirmed = head - CONFIRM_DEPTH
    st.update(pending_tip=head, last_confirmed=last_confirmed, confirm_depth=CONFIRM_DEPTH)
    lp = st["last_processed"]
    if lp >= last_confirmed:  # nothing old enough yet -> wait, do not touch the cursor
        st["updated_at"] = time.time()
        save_state(st)
        return {"head": head, "skipped": True, "states": {}}
    a, b = lp + 1, min(last_confirmed, lp + MAX_RANGE)
    used, settled, unavailable = classify_range(a, b)
    det = time.time()
    states = {bn: s for bn, s, _ in settled}
    if unavailable:
        states[unavailable[0]] = BlockState.UNAVAILABLE
        st["block_states"][BlockState.UNAVAILABLE] += 1
        event("unavailable", block=unavailable[0], reason=unavailable[1], head=head)
    recs = []
    for bn, s, logs in settled:
        st["block_states"][s] += 1
        for l in logs:
            if len(l.get("topics", [])) < 4:
                continue
            if wallets is not None and ("0x" + l["topics"][2][-40:]).lower() not in wallets:
                continue
            r = decode(l)
            bts, bsrc = block_ts(l)
            r.update(block_ts=bts, detect_ts=round(det, 3), latency_s=round(det - bts, 3),
                     head=head, rpc=RPCS.index(used), input_hash=sha(l), code_hash=CODE_HASH)
            if bsrc != "log":
                r["ts_src"] = bsrc
            r["output_hash"] = sha(r)
            recs.append(r)
    _bts.clear()
    if recs:
        write_ledger(det, recs)
    if settled:  # cursor moves only over DATA_PRESENT / CONFIRMED_EMPTY blocks
        st["last_processed"] = settled[-1][0]
    st["next_block"] = st["last_processed"] + 1
    st["fills"] += len(recs)
    st["updated_at"] = det
    save_state(st)
    return {"head": head, "skipped": False, "states": states, "n_recs": len(recs), "logs_seen": sum(len(x[2]) for x in settled)}


def main():
    os.makedirs(LEDGER_DIR, exist_ok=True)
    wallets = None
    if TRACK != "all":
        wallets = {w.lower() for w in json.load(open(WALLETS_FILE))}
    st = load_state()
    if st is None:
        head = int(rpc("eth_blockNumber", [])[0], 16)
        st = {"start_ts": time.time(), "next_block": head - CONFIRM_DEPTH, "fills": 0, "code_hash_first": CODE_HASH}
        migrate_state(st)
        save_state(st)
        event("start_new", next_block=st["next_block"], track=TRACK, n_wallets=len(wallets or []), duration_h=DURATION_H, confirm_depth=CONFIRM_DEPTH)
    else:
        migrate_state(st)
        event("resume", next_block=st["next_block"], fills=st["fills"], confirm_depth=CONFIRM_DEPTH)
    end_ts = st["start_ts"] + DURATION_H * 3600
    if time.time() >= end_ts:
        log("run already finished (DURATION_H reached); exiting 0")
        return
    log(f"listener v3 code_hash={CODE_HASH[:12]} track={TRACK} wallets={len(wallets or [])} ledger={LEDGER_DIR} next_block={st['next_block']} confirm_depth={CONFIRM_DEPTH}")
    errs = 0; last_status = 0; polls = 0; global_fills = 0
    while time.time() < end_ts:
        try:
            res = poll_once(st, wallets)
            if res["skipped"]:
                time.sleep(max(POLL_S, 1)); continue
            polls += 1; global_fills += res["logs_seen"]
            errs = 0
            now = time.time()
            if now - last_status > 30:
                last_status = now
                free_mb = shutil.disk_usage(LEDGER_DIR).free / 1e6
                if free_mb < MIN_FREE_MB:
                    event("disk_low_stop", free_mb=round(free_mb, 1), fills=st["fills"])
                    log(f"disk almost full ({free_mb:.0f} MB free) -> clean stop")
                    break
                with open(STATUS, "w") as f:
                    json.dump({"ts": now, "head": res["head"], "next_block": st["next_block"],
                               "last_processed": st["last_processed"], "last_confirmed": st["last_confirmed"],
                               "pending_tip": st["pending_tip"], "confirm_depth": CONFIRM_DEPTH,
                               "lag_blocks": res["head"] - st["last_processed"], "block_states": st["block_states"],
                               "fills_total": st["fills"], "polls_since_boot": polls,
                               "global_fills_since_boot": global_fills, "free_mb": round(free_mb, 1), "ends_at": end_ts}, f)
                log(f"head={res['head']} last_processed={st['last_processed']} fills_total={st['fills']} states={st['block_states']}")
        except Exception as e:
            errs += 1
            event("error", err=repr(e)[:500], consecutive=errs)
            log(f"error #{errs}: {e!r}"[:300])
            time.sleep(min(60, 3 * errs))
            continue
        time.sleep(POLL_S)
    close_ledger()
    event("finished", fills=st["fills"], next_block=st["next_block"])
    open(os.path.join(LEDGER_DIR, "chain_listener_DONE"), "w").write(str(time.time()))
    log("finished: DURATION_H reached")


def _term(*_):
    raise SystemExit(0)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, _term)  # Railway sends SIGTERM on redeploy/stop
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        close_ledger()  # finish the gzip member cleanly
