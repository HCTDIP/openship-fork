"""Highlights + chain re-verify for chain listener v2 ledgers (offline, read-only).

  python highlights.py [LEDGER_DIR] [--reverify N]

Prints one HL_JSON line (machine-readable) and a short human summary.
- top fills by USDC, bursts (same wallet+token, >=5 distinct tx within 60s),
  extreme-price (<0.05 or >0.95) large fills, anomalies (latency >5s, events)
- re-verify: re-fetch N random records' logs from the chain and recompute input_hash
"""
import glob, hashlib, json, os, random, sys, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from report import read_lines, canon

TOPIC = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
RPCS = [u.strip() for u in os.environ.get("RPCS", "https://polygon.drpc.org,https://1rpc.io/matic").split(",") if u.strip()]


def rpc(u, method, params):
    req = urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read()).get("result")


def questions(tokens):
    out = {}
    toks = list(tokens)
    for closed in ("false", "true"):
        todo = [t for t in toks if t not in out]
        for i in range(0, len(todo), 50):
            q = urllib.parse.urlencode([("clob_token_ids", t) for t in todo[i:i + 50]] + [("limit", "500"), ("closed", closed)])
            try:
                ms = json.loads(urllib.request.urlopen(urllib.request.Request(
                    f"https://gamma-api.polymarket.com/markets?{q}", headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
            except Exception:
                continue
            for m in ms:
                ids = m.get("clobTokenIds")
                ids = json.loads(ids) if isinstance(ids, str) else (ids or [])
                outs = m.get("outcomes")
                outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
                for j, t in enumerate(ids):
                    out[str(t)] = (m.get("question") or "")[:90] + (f" [{outs[j]}]" if j < len(outs) else "")
    return out


def reverify(recs, n):
    sample = random.Random(42).sample(recs, min(n, len(recs)))
    ok = bad = miss = err = 0
    bad_ex = []
    for r in sample:
        u = RPCS[r.get("rpc", 0)] if r.get("rpc", 0) < len(RPCS) else RPCS[0]
        try:
            logs = rpc(u, "eth_getLogs", [{"fromBlock": hex(r["block"]), "toBlock": hex(r["block"]), "topics": [TOPIC]}])
        except Exception:
            err += 1; continue
        if logs is None:
            err += 1; continue
        hit = [l for l in logs if l["transactionHash"] == r["tx"] and int(l["logIndex"], 16) == r["log_index"]]
        if not hit:
            miss += 1; bad_ex.append(r["tx"]); continue
        if hashlib.sha256(canon(hit[0]).encode()).hexdigest() == r["input_hash"]:
            ok += 1
        else:
            bad += 1; bad_ex.append(r["tx"])
        time.sleep(0.1)
    return {"sampled": len(sample), "match": ok, "mismatch": bad, "missing_on_chain": miss, "rpc_error": err, "examples": bad_ex[:5]}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    d = args[0] if args else os.environ.get("LEDGER_DIR", "/var/minis/shared/ledgers")
    nrev = int(sys.argv[sys.argv.index("--reverify") + 1]) if "--reverify" in sys.argv else 50
    files = sorted(glob.glob(os.path.join(d, "chain_listener_2*.jsonl*")))
    recs, seen = [], set()
    for fn in files:
        for line in read_lines(fn):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = (r["tx"], r["log_index"])
            if k in seen:
                continue
            seen.add(k); recs.append(r)
    recs.sort(key=lambda r: (r["block"], r["log_index"]))
    slim = lambda r: {k: r[k] for k in ("wallet", "side", "token_id", "price", "usdc", "shares", "block_ts", "latency_s", "tx")}

    top = sorted(recs, key=lambda r: -r["usdc"])[:20]
    ext = sorted([r for r in recs if r["price"] is not None and (r["price"] < 0.05 or r["price"] > 0.95)], key=lambda r: -r["usdc"])[:10]
    n_ext = sum(1 for r in recs if r["price"] is not None and (r["price"] < 0.05 or r["price"] > 0.95))

    # bursts: same wallet+token, >=5 distinct tx inside any 60s window -> one cluster per (wallet, token)
    by = defaultdict(list)
    for r in recs:
        by[(r["wallet"], r["token_id"])].append(r)
    bursts = []
    for (w, t), rs in by.items():
        best = None; j = 0
        for i in range(len(rs)):
            while rs[i]["block_ts"] - rs[j]["block_ts"] > 60:
                j += 1
            win = rs[j:i + 1]
            ntx = len({x["tx"] for x in win})
            if ntx >= 5 and (best is None or ntx > best[0]):
                best = (ntx, win)
        if best:
            win = best[1]
            bursts.append({"wallet": w, "token_id": t, "tx_in_60s": best[0], "usdc": round(sum(x["usdc"] for x in win), 2),
                           "sides": dict(Counter(x["side"] for x in win)), "first_ts": win[0]["block_ts"],
                           "px_first": win[0]["price"], "px_last": win[-1]["price"], "tx": win[0]["tx"]})
    bursts.sort(key=lambda b: -b["usdc"])

    lat5 = [r for r in recs if r["latency_s"] > 5]
    ev = Counter()
    evf = os.path.join(d, "chain_listener_events.jsonl")
    if os.path.exists(evf):
        ev = Counter(json.loads(l)["ev"] for l in open(evf))
    q = questions({r["token_id"] for r in top + ext} | {b["token_id"] for b in bursts[:10]})
    for lst in (top, ext):
        for r in lst:
            r["market"] = q.get(r["token_id"], "?")
    for b in bursts[:10]:
        b["market"] = q.get(b["token_id"], "?")

    size = sum(os.path.getsize(f) for f in files)
    span_h = (recs[-1]["block_ts"] - recs[0]["block_ts"]) / 3600 if recs else 0
    out = {
        "fills": len(recs), "span_h": round(span_h, 3), "ledger_bytes": size,
        "proj_72h_mb": round(size / 1e6 / span_h * 72, 1) if span_h else None,
        "wallets_active": len({r["wallet"] for r in recs}), "usdc_total": round(sum(r["usdc"] for r in recs), 2),
        "top_fills": [dict(slim(r), market=r["market"]) for r in top],
        "extreme_count": n_ext, "extreme_top": [dict(slim(r), market=r["market"]) for r in ext],
        "bursts_count": len(bursts), "bursts_top": bursts[:10],
        "latency_over_5s": len(lat5), "latency_over_5s_ex": [slim(r) for r in sorted(lat5, key=lambda r: -r["latency_s"])[:5]],
        "events": dict(ev),
    }
    wu = Counter()
    for r in recs:
        wu[r["wallet"]] += r["usdc"]
    out["top_wallets_usdc"] = [(w, round(v, 2)) for w, v in wu.most_common(10)]
    out["reverify"] = reverify(recs, nrev) if recs else None
    J = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    lists = ("top_fills", "extreme_top", "bursts_top", "latency_over_5s_ex", "top_wallets_usdc")
    print("HL_SUM " + J({k: v for k, v in out.items() if k not in lists}), flush=True)
    for k in lists:
        for i, it in enumerate(out[k]):
            print(f"HL_{k} {i} " + J(it), flush=True)
    print("HL_END", flush=True)

if __name__ == "__main__":
    main()
