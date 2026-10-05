"""Dirty-data injection harness for jevaudit reconcile. Never touches the production ledger:
reads ledger_original/ (byte copy of openship-fork raw/ledger/), writes only under dirty-ledger/,
restored/, reconcile/, fixtures/.

  python injector.py setup                 copy production -> ledger_original/, check SHA256SUMS
  python injector.py prepare  <fid>        ledger_original -> dirty-ledger/<fid>/ (byte copy)
  python injector.py inject   <fid>        apply fault, write fixtures/<fid>.json (undo log + sha)
  python injector.py reconcile <fid>       run jevaudit check, write reconcile/<fid>.json
  python injector.py restore  <fid>        undo fault -> restored/<fid>/, sha must equal original
  python injector.py case61                (only after all pass) reproduce the real 61-fill gap
  python injector.py summary
"""
import gzip, hashlib, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # openship-fork checkout
PROD = os.path.join(ROOT, "raw", "ledger")                         # production ledger: read only
SUMS = os.path.join(ROOT, "SHA256SUMS.txt")
if os.environ.get("JEVAUDIT_SRC"):                                  # else: pip install jevaudit (with audit/)
    sys.path.insert(0, os.environ["JEVAUDIT_SRC"])
from jevaudit.audit.chain import reconcile_with_chain, verify_ledger_integrity, sha  # noqa: E402
from jevaudit.audit.findings import check_partitions, classify  # noqa: E402

ORIG = os.path.join(HERE, "ledger_original")
DAYS = ["chain_listener_20261002.jsonl.gz", "chain_listener_20261003.jsonl.gz"]
META = ["chain_listener_state.json", "chain_listener_status.json", "chain_listener_events.jsonl"]
TARGET = DAYS[1]
CODE = ["68e29fd1e5973ded6efd7c8eab14501104450f81393521c9d273d816f7349b36"]
WALLETS = json.load(open(os.path.join(ROOT, "listener", "wallets.json")))
W = (94860000, 94860039)          # clean middle window (no known gap / duplicate)
STATE = json.load(open(os.path.join(PROD, "chain_listener_state.json")))
STATUS = json.load(open(os.path.join(PROD, "chain_listener_status.json")))
EXPECTED_LAST = STATE["next_block"] - 1  # 94883047, from the listener checkpoint
TAIL = (EXPECTED_LAST - 39, EXPECTED_LAST)
N_TAIL = 5
P = lambda *a: os.path.join(HERE, *a)


def fsha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def csha(path):  # sha256 of the decompressed content
    h = hashlib.sha256()
    with gzip.open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def blk(line):
    return int(line[9:line.index(",")])


def read_lines(path):
    with gzip.open(path, "rt") as f:
        return f.readlines()


def write_lines(path, lines):
    with gzip.open(path, "wt", compresslevel=1) as f:
        f.writelines(lines)


def setup():
    os.makedirs(ORIG, exist_ok=True)
    sums = {l.split()[1]: l.split()[0] for l in open(SUMS) if l.strip()}
    out = {}
    for n in DAYS + META:
        shutil.copyfile(os.path.join(PROD, n), os.path.join(ORIG, n))
        s = fsha(os.path.join(ORIG, n))
        assert s == sums["raw/ledger/" + n], f"copy of {n} != SHA256SUMS"
        out[n] = {"file_sha256": s}
    out[TARGET]["content_sha256"] = csha(os.path.join(ORIG, TARGET))
    json.dump(out, open(P("ledger_original.sha256.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


def orig_sha():
    return json.load(open(P("ledger_original.sha256.json")))


def prepare(fid):
    d = P("dirty-ledger", fid)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for n in DAYS:
        shutil.copyfile(os.path.join(ORIG, n), os.path.join(d, n))


def by_block(lines, lo, hi):
    out = {}
    for i, l in enumerate(lines):
        b = blk(l)
        if lo <= b <= hi:
            out.setdefault(b, []).append(i)
    return out


def inject(fid):
    d = P("dirty-ledger", fid)
    tgt = os.path.join(d, TARGET)
    fx = {"fixture_id": fid, "target": TARGET, "original_file_sha256": orig_sha()[TARGET]["file_sha256"],
          "original_content_sha256": orig_sha()[TARGET]["content_sha256"], "ops": []}
    if fid == "T5":
        fx["inject"] = "nothing"
        fx["expected"] = "PASS (no finding) on middle window, tail window, partitions, integrity"
    elif fid == "T3.6":
        os.remove(tgt)
        fx["inject"] = f"delete file {TARGET}"
        fx["expected"] = "exactly 1 x MISSING_DAY_PARTITION day=20261003"
    else:
        lines = read_lines(tgt)
        bb = by_block(lines, *W)
        multi = sorted(b for b, ix in bb.items() if len(ix) >= 3)
        if fid == "T1":
            b = multi[0]
            i = bb[b][-1]
            r = json.loads(lines[i])
            r["tx"] = "0x" + hashlib.sha256(b"T1-fake-fill").hexdigest()
            r.pop("output_hash")
            r["output_hash"] = sha(r)  # valid fingerprint: only the chain can expose it
            new = json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            lines.insert(i + 1, new)
            fx["ops"] = [{"op": "insert", "index": i + 1, "line": new}]
            fx["inject"] = f"1 fake fill in block {b}, tx {r['tx']}, log_index {r['log_index']} (output_hash recomputed)"
            fx["expected"] = f"exactly 1 x EXTRA_IN_LEDGER block={b} tx={r['tx']}"
            fx["want"] = {"code": "EXTRA_IN_LEDGER", "block": b, "tx": r["tx"], "log_index": r["log_index"]}
        elif fid == "T2":
            b = multi[1]
            i = bb[b][0]
            r = json.loads(lines[i])
            fx["ops"] = [{"op": "delete", "index": i, "line": lines.pop(i)}]
            fx["inject"] = f"delete 1 real fill: block {b}, tx {r['tx']}, log_index {r['log_index']}"
            fx["expected"] = f"exactly 1 x MISSING_FROM_LEDGER block={b} tx={r['tx']}"
            fx["want"] = {"code": "MISSING_FROM_LEDGER", "block": b, "tx": r["tx"], "log_index": r["log_index"]}
        elif fid == "T3":
            b = multi[2]
            ix = bb[b]
            fx["ops"] = [{"op": "delete", "index": i, "line": lines[i]} for i in ix]
            for i in reversed(ix):
                lines.pop(i)
            fx["inject"] = f"delete all {len(ix)} fills of block {b}"
            fx["expected"] = f"exactly 1 x MISSING_BLOCK block={b} missing_fills={len(ix)}"
            fx["want"] = {"code": "MISSING_BLOCK", "block": b, "missing_fills": len(ix)}
        elif fid == "T3.5":
            last = sorted({blk(l) for l in lines[-2000:]})[-N_TAIL:]
            ix = [i for i in range(len(lines)) if blk(lines[i]) in set(last)]
            fx["ops"] = [{"op": "delete", "index": i, "line": lines[i]} for i in ix]
            for i in reversed(ix):
                lines.pop(i)
            fx["inject"] = f"delete last {N_TAIL} blocks {last[0]}..{last[-1]} ({len(ix)} fills)"
            fx["expected"] = f"exactly 1 x TRAILING_GAP blocks={last} missing_fills={len(ix)}"
            fx["want"] = {"code": "TRAILING_GAP", "blocks": last, "missing_fills": len(ix)}
        write_lines(tgt, lines)
    fx["modified_file_sha256"] = fsha(tgt) if os.path.exists(tgt) else "(file deleted)"
    fx["modified_content_sha256"] = csha(tgt) if os.path.exists(tgt) else "(file deleted)"
    os.makedirs(P("fixtures"), exist_ok=True)
    json.dump(fx, open(P("fixtures", fid + ".json"), "w"), indent=1)
    print(fid, fx["inject"])


def load(d, lo, hi):
    recs, last = [], None
    for n in DAYS:
        p = os.path.join(d, n)
        if not os.path.exists(p):
            continue
        with gzip.open(p, "rt") as f:
            for l in f:
                b = blk(l)
                last = b if last is None or b > last else last
                if lo <= b <= hi:
                    recs.append(json.loads(l))
    return recs, last


def check(d, lo, hi):
    recs, last = load(d, lo, hi)
    integ = verify_ledger_integrity(recs, CODE)
    rep = reconcile_with_chain(recs, lo=lo, hi=hi, wallets=WALLETS, step=10, sample_blocks=10)
    return {"range": [lo, hi], "ledger_last_block": last, "integrity": {k: integ[k] for k in ("ok", "n_records", "n_failed")},
            "counts": rep["counts"], "multi_node_checked_blocks": rep["multi_node_checked_blocks"],
            "node_agreement_sample": rep["node_agreement_sample"], "elapsed_s": rep["elapsed_s"],
            "findings": classify(rep, last, integ)}


def present(d):
    return [n for n in DAYS if os.path.exists(os.path.join(d, n))]


def reconcile(fid):
    d = P("dirty-ledger", fid)
    fx = json.load(open(P("fixtures", fid + ".json")))
    parts = check_partitions(present(d), STATE["start_ts"], STATUS["ends_at"])
    out = {"fixture_id": fid, "partitions": {k: parts[k] for k in ("expected", "present", "missing")}}
    if fid in ("T1", "T2", "T3"):
        out["middle"] = check(d, *W)
        got = out["middle"]["findings"]
        w = fx["want"]
        ok = len(got) == 1 and all(got[0].get(k) == v for k, v in w.items()) and not parts["missing"]
    elif fid == "T3.5":
        out["tail"] = check(d, *TAIL)
        got = out["tail"]["findings"]
        w = fx["want"]
        ok = len(got) == 1 and all(got[0].get(k) == v for k, v in w.items()) and not parts["missing"]
    elif fid == "T3.6":
        got = parts["findings"]
        ok = len(got) == 1 and got[0]["day"] == "20261003"
    else:  # T5
        out["middle"] = check(d, *W)
        out["tail"] = check(d, *TAIL)
        got = out["middle"]["findings"] + out["tail"]["findings"] + parts["findings"]
        ok = got == [{"code": "PASS"}, {"code": "PASS"}] and out["middle"]["integrity"]["ok"] and out["tail"]["integrity"]["ok"]
    out["actual"] = got
    out["pass"] = bool(ok)
    os.makedirs(P("reconcile"), exist_ok=True)
    json.dump(out, open(P("reconcile", fid + ".json"), "w"), indent=1)
    print(fid, "PASS" if ok else "FAIL", json.dumps(got)[:400])


def restore(fid):
    d, r = P("dirty-ledger", fid), P("restored", fid)
    shutil.rmtree(r, ignore_errors=True)
    os.makedirs(r)
    fx = json.load(open(P("fixtures", fid + ".json")))
    o = orig_sha()
    for n in DAYS:
        src = os.path.join(d, n)
        if n == TARGET and fid == "T3.6":
            shutil.copyfile(os.path.join(ORIG, n), os.path.join(r, n))  # file-level fault: restore = copy back
        elif n == TARGET and fx["ops"]:
            lines = read_lines(src)
            for op in reversed(fx["ops"]) if fx["ops"][0]["op"] == "insert" else []:
                assert lines[op["index"]] == op["line"]
                lines.pop(op["index"])
            for op in fx["ops"] if fx["ops"][0]["op"] == "delete" else []:
                lines.insert(op["index"], op["line"])
            write_lines(os.path.join(r, n), lines)
        else:
            shutil.copyfile(src, os.path.join(r, n))
    res = {}
    for n in DAYS:
        p = os.path.join(r, n)
        if n == TARGET and fx["ops"]:  # re-gzipped -> bytes differ by design, compare decompressed content
            got, want, kind = csha(p), o[n]["content_sha256"], "content"
        else:
            got, want, kind = fsha(p), o[n]["file_sha256"], "file"
        res[n] = {"kind": kind, "restored_sha256": got, "original_sha256": want, "equal": got == want}
    prod = {n: fsha(os.path.join(PROD, n)) == o[n]["file_sha256"] for n in DAYS}
    fx["restore"] = res
    fx["production_untouched"] = prod
    json.dump(fx, open(P("fixtures", fid + ".json"), "w"), indent=1)
    ok = all(v["equal"] for v in res.values()) and all(prod.values())
    print(fid, "RESTORE", "OK" if ok else "MISMATCH")
    if ok:  # evidence kept in fixtures/<fid>.json; drop the bulky copies
        shutil.rmtree(d)
        shutil.rmtree(r)
    sys.exit(0 if ok else 1)


CASE = {94834428: 16, 94840332: 12, 94862944: 1, 94875380: 29, 94875386: 3}


def case61():
    rows = []
    for bn, want in CASE.items():
        recs, last = load(ORIG, bn - 1, bn)
        integ = verify_ledger_integrity(recs, CODE)
        rep = reconcile_with_chain([r for r in recs if r["block"] == bn] or recs, lo=bn, hi=bn, wallets=WALLETS, step=1, sample_blocks=1)
        f = classify(rep, last)
        miss = sum(1 for x in f if x["code"] == "MISSING_FROM_LEDGER") + sum(x.get("missing_fills", 0) for x in f if x["code"] == "MISSING_BLOCK")
        dups = sum(1 for x in integ["failures"] if "duplicate tx/log_index" in x["reasons"] and x["block"] == bn - 1)
        rows.append({"block": bn, "missing_found": miss, "missing_expected": want, "dups_in_prev_block": dups,
                     "codes": sorted({x["code"] for x in f}), "match": miss == want})
        print(rows[-1])
    json.dump(rows, open(P("reconcile", "case61.json"), "w"), indent=1)


def summary():
    r = {fid: json.load(open(P("reconcile", fid + ".json")))["pass"] for fid in ("T1", "T2", "T3", "T3.5", "T3.6", "T5")}
    t4 = json.load(open(P("reconcile", "T4.json")))
    r["T4"] = bool(t4.get("T4a") and t4.get("T4b"))
    r["T4c"] = bool(t4.get("T4c"))
    if os.path.exists(P("reconcile", "case61.json")):
        r["61/61"] = all(x["match"] for x in json.load(open(P("reconcile", "case61.json"))))
    print(json.dumps(r))


if __name__ == "__main__":
    fn = globals()[sys.argv[1]]
    fn(*sys.argv[2:])
