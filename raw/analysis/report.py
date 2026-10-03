"""Offline report for chain listener v2 ledgers. Run AFTER (or during) the run.

  python report.py [LEDGER_DIR] [--enrich]

--enrich: map token_id -> market (question / condition_id) via Gamma API. This is the
only network call and happens offline, never inside the listener.
Writes chain_listener_report.json + .md next to the ledgers.
"""
import glob, hashlib, json, os, sys, time, urllib.parse, urllib.request, zlib
from collections import Counter


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 3)


def _members(data):
    """Decompress a multi-member gzip whose members may be cut off by a crash.
    The listener sync-flushes every poll, so a cut member is readable up to the cut;
    the next member's header then shows up as an invalid deflate block."""
    MAGIC, CH, pos = b"\x1f\x8b\x08", 1 << 15, 0
    while 0 <= pos < len(data):
        d = zlib.decompressobj(31)
        i, end, err_at = pos, None, None
        while i < len(data):
            chunk = data[i:i + CH]
            snap = d.copy()
            try:
                yield d.decompress(chunk)
                if d.eof:
                    end = i + len(chunk) - len(d.unused_data); break
                i += len(chunk)
            except zlib.error:
                d = snap  # replay byte by byte to keep everything before the cut
                for j in range(len(chunk)):
                    try:
                        yield d.decompress(chunk[j:j + 1])
                    except zlib.error:
                        err_at = i + j; break
                    if d.eof:
                        end = i + j + 1; break
                break
        yield b"\n"  # never glue a torn line onto the next member
        if end is not None:
            pos = end
        elif err_at is not None:
            pos = data.find(MAGIC, max(pos + 10, err_at - 8))
        else:
            return  # file ends mid-member (still being written)


def read_lines(fn):
    if not fn.endswith(".gz"):
        yield from open(fn, "rb"); return
    buf = b""
    for out in _members(open(fn, "rb").read()):
        buf += out
        *lines, buf = buf.split(b"\n")
        yield from (l for l in lines if l)
    if buf:
        yield buf


def enrich(tokens):
    out = {}
    toks = list(tokens)
    for closed in ("false", "true"):
        todo = [t for t in toks if t not in out]
        for i in range(0, len(todo), 50):
            q = urllib.parse.urlencode([("clob_token_ids", t) for t in todo[i:i + 50]] + [("limit", "500"), ("closed", closed)])
            req = urllib.request.Request(f"https://gamma-api.polymarket.com/markets?{q}", headers={"User-Agent": "Mozilla/5.0"})
            try:
                ms = json.loads(urllib.request.urlopen(req, timeout=30).read())
            except Exception:
                continue
            for m in ms:
                ids = m.get("clobTokenIds")
                ids = json.loads(ids) if isinstance(ids, str) else (ids or [])
                for t in ids:
                    out[str(t)] = {"market_id": m.get("id"), "condition_id": m.get("conditionId"), "question": m.get("question")}
            time.sleep(0.2)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    d = args[0] if args else os.environ.get("LEDGER_DIR", "/var/minis/shared/ledgers")
    files = sorted(glob.glob(os.path.join(d, "chain_listener_2*.jsonl*")))
    lat, sides, exch, toks, wallets, hours, srcs, codes = [], Counter(), Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    bad_out, n, usdc, seen, dup = 0, 0, 0.0, set(), 0
    for fn in files:
        for line in read_lines(fn):
            try:
                r = json.loads(line)
            except ValueError:
                continue  # torn last line after a crash
            k = (r["tx"], r["log_index"])
            if k in seen:
                dup += 1; continue  # re-processed block range after a crash
            seen.add(k)
            n += 1
            oh = r.pop("output_hash")
            if hashlib.sha256(canon(r).encode()).hexdigest() != oh:
                bad_out += 1
            lat.append(r["latency_s"]); sides[r["side"]] += 1; exch[r["exchange"]] += 1
            toks[r["token_id"]] += 1; wallets[r["wallet"]] += 1; srcs[r.get("ts_src", "log")] += 1; codes[r["code_hash"]] += 1
            hours[time.strftime("%Y-%m-%d %H:00", time.gmtime(r["block_ts"]))] += 1
            usdc += r["usdc"]
    rep = {
        "files": [os.path.basename(f) for f in files], "fills": n, "duplicates": dup,
        "output_hash_mismatch": bad_out, "code_hashes": dict(codes), "usdc_total": round(usdc, 2),
        "latency_s": {"min": pct(lat, 0), "p50": pct(lat, .5), "p90": pct(lat, .9), "p95": pct(lat, .95),
                      "p99": pct(lat, .99), "max": pct(lat, 1), "share_over_60s": round(sum(x > 60 for x in lat) / len(lat), 5) if lat else None},
        "block_ts_source": dict(srcs), "sides": dict(sides), "exchanges": dict(exch),
        "unique_wallets": len(wallets), "unique_tokens": len(toks),
        "hours_covered": len(hours), "fills_per_hour_min": min(hours.values()) if hours else 0,
        "top_tokens": toks.most_common(20),
    }
    if "--enrich" in sys.argv:
        mp = enrich([t for t, _ in toks.most_common(2000)])
        mk = Counter()
        for t, c in toks.items():
            mk[(mp.get(t) or {}).get("question") or "unknown"] += c
        rep["top_markets"] = mk.most_common(20)
        rep["token_market_map_coverage"] = round(sum(c for t, c in toks.items() if t in mp) / n, 4) if n else None
    st = os.path.join(d, "chain_listener_events.jsonl")
    if os.path.exists(st):
        ev = Counter(json.loads(l)["ev"] for l in open(st))
        rep["events"] = dict(ev)
    json.dump(rep, open(os.path.join(d, "chain_listener_report.json"), "w"), indent=1, ensure_ascii=False)
    L = rep["latency_s"]
    md = [f"# chain listener v2 report ({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())})", "",
          f"- fills: {n} (dup {dup}, output_hash mismatch {bad_out}), USDC {rep['usdc_total']}",
          f"- latency s: p50 {L['p50']} / p95 {L['p95']} / p99 {L['p99']} / max {L['max']}, >60s share {L['share_over_60s']}",
          f"- hours covered: {rep['hours_covered']}, min fills/hour {rep['fills_per_hour_min']}",
          f"- sides {rep['sides']}; wallets {rep['unique_wallets']}; tokens {rep['unique_tokens']}",
          f"- code_hashes {rep['code_hashes']}; events {rep.get('events')}",
          "", "note: block_ts is whole seconds and latency depends on the host clock, so small negatives are normal."]
    open(os.path.join(d, "chain_listener_report.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
