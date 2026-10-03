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
RPCS = [u.strip() for u in os.environ.get("RPCS", "https://polygon.drpc.org,https://1rpc.io/matic").split(",") if u.strip()]
DURATION_H = float(os.environ.get("DURATION_H", "72"))
TRACK = os.environ.get("TRACK", "wallets")  # wallets | all
WALLETS_FILE = os.environ.get("WALLETS_FILE", os.path.join(HERE, "wallets.json"))
POLL_S = float(os.environ.get("POLL_S", "0.5"))
GZ = os.environ.get("GZIP", "1") != "0"
MIN_FREE_MB = float(os.environ.get("MIN_FREE_MB", "30"))  # stop cleanly before the volume fills up
MAX_RANGE = int(os.environ.get("MAX_RANGE", "30"))  # blocks per getLogs
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


def rpc(method, params):
    err = None
    for u in RPCS:
        try:
            req = urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.loads(r.read())
            if "result" in res and res["result"] is not None:
                return res["result"], u
            err = res.get("error")
        except Exception as e:
            err = repr(e)
    raise RuntimeError(f"all rpc failed: {err}")


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


def main():
    os.makedirs(LEDGER_DIR, exist_ok=True)
    wallets = None
    if TRACK != "all":
        wallets = {w.lower() for w in json.load(open(WALLETS_FILE))}
    st = load_state()
    if st is None:
        head = int(rpc("eth_blockNumber", [])[0], 16)
        st = {"start_ts": time.time(), "next_block": head - 2, "fills": 0, "code_hash_first": CODE_HASH}
        save_state(st)
        event("start_new", next_block=st["next_block"], track=TRACK, n_wallets=len(wallets or []), duration_h=DURATION_H)
    else:
        event("resume", next_block=st["next_block"], fills=st["fills"])
    end_ts = st["start_ts"] + DURATION_H * 3600
    if time.time() >= end_ts:
        log("run already finished (DURATION_H reached); exiting 0")
        return
    log(f"listener v2 code_hash={CODE_HASH[:12]} track={TRACK} wallets={len(wallets or [])} ledger={LEDGER_DIR} next_block={st['next_block']}")
    errs = 0; last_status = 0; polls = 0; global_fills = 0
    while time.time() < end_ts:
        try:
            head = int(rpc("eth_blockNumber", [])[0], 16)
            nxt = st["next_block"]
            if head < nxt:
                time.sleep(1); continue
            to = min(head, nxt + MAX_RANGE - 1)
            logs, used = rpc("eth_getLogs", [{"fromBlock": hex(nxt), "toBlock": hex(to), "topics": [TOPIC]}])
            det = time.time()
            polls += 1; global_fills += len(logs)
            recs = []
            for l in logs:
                if len(l.get("topics", [])) < 4 or l["address"].lower() not in EXCHANGES:
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
            st["next_block"] = to + 1
            st["fills"] += len(recs)
            save_state(st)
            errs = 0
            if det - last_status > 30:
                last_status = det
                free_mb = shutil.disk_usage(LEDGER_DIR).free / 1e6
                if free_mb < MIN_FREE_MB:
                    event("disk_low_stop", free_mb=round(free_mb, 1), fills=st["fills"])
                    log(f"disk almost full ({free_mb:.0f} MB free) -> clean stop")
                    break
                with open(STATUS, "w") as f:
                    json.dump({"ts": det, "head": head, "next_block": st["next_block"], "lag_blocks": head - to,
                               "fills_total": st["fills"], "polls_since_boot": polls,
                               "global_fills_since_boot": global_fills, "free_mb": round(free_mb, 1), "ends_at": end_ts}, f)
                log(f"head={head} behind={head - to} fills_total={st['fills']}")
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
