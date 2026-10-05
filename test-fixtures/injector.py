"""Dirty-data injector for the jevaudit capability test. Never touches raw/.

python injector.py --orig ledger_original.jsonl.gz --test T1 --out dirty-ledger/T1 --manifest dirty-ledger/T1.manifest.json

Writes the (possibly modified) ledger as day partitions chain_listener_<YYYYMMDD>.jsonl.gz, split by
detect_ts UTC date exactly like listener.py. gzip is deterministic (mtime=0, no filename) so
SHA256 of restored partitions can be compared byte for byte.
Tests: NONE (baseline, also used for T5), T1 forge, T2 delete one fill, T3 delete one middle block,
T35 delete last N blocks, T36 delete the 20261003 partition.
"""
import argparse, gzip, hashlib, json, os, time

T35_N = 5
T3_BLOCK = 94853640          # middle of the window, last block of the 20261002 partition side
T2_BLOCK = 94853600
T1_BLOCK = 94853700


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest")
    a = ap.parse_args()
    raw = gzip.open(a.orig, "rb").read()
    lines = [l for l in raw.split(b"\n") if l]
    recs = [json.loads(l) for l in lines]
    keep = list(range(len(lines)))  # indexes of original lines kept, byte-for-byte
    added = []                      # new (forged) lines
    inj = {"test": a.test}
    blocks = sorted({r["block"] for r in recs})
    if a.test == "T1":
        src = next(r for r in recs if r["block"] == T1_BLOCK)
        f = dict(src)
        f.pop("output_hash")
        f["tx"] = "0x" + sha(b"jevaudit-T1-forged-fill")
        f["log_index"] = 9999
        f["input_hash"] = sha(b"forged-input")
        f["output_hash"] = hashlib.sha256(canon(f).encode()).hexdigest()  # internally consistent forgery
        added.append((src["detect_ts"], canon(f).encode()))
        inj["forged"] = {"block": f["block"], "tx": f["tx"], "log_index": f["log_index"], "wallet": f["wallet"]}
    elif a.test == "T2":
        i = next(i for i, r in enumerate(recs) if r["block"] == T2_BLOCK)
        n_in_block = sum(1 for r in recs if r["block"] == T2_BLOCK)
        keep.remove(i)
        inj["deleted"] = {"block": T2_BLOCK, "tx": recs[i]["tx"], "log_index": recs[i]["log_index"], "fills_left_in_block": n_in_block - 1}
    elif a.test == "T3":
        idx = [i for i, r in enumerate(recs) if r["block"] == T3_BLOCK]
        keep = [i for i in keep if i not in set(idx)]
        inj["deleted_block"] = {"block": T3_BLOCK, "fills": len(idx)}
    elif a.test == "T35":
        tail = blocks[-T35_N:]
        idx = {i for i, r in enumerate(recs) if r["block"] in tail}
        keep = [i for i in keep if i not in idx]
        inj["deleted_tail"] = {"blocks": tail, "from": tail[0], "to": tail[-1], "fills": len(idx)}
    elif a.test in ("NONE", "T5", "T36"):
        pass
    else:
        raise SystemExit(f"unknown test {a.test}")

    parts = {}
    for i in keep:
        d = time.strftime("%Y%m%d", time.gmtime(recs[i]["detect_ts"]))
        parts.setdefault(d, []).append(lines[i])
    for ts, l in added:
        d = time.strftime("%Y%m%d", time.gmtime(ts))
        parts.setdefault(d, []).append(l)
    if a.test == "T36":
        n = len(parts.pop("20261003", []))
        inj["deleted_partition"] = {"file": "chain_listener_20261003.jsonl.gz", "fills": n}
    os.makedirs(a.out, exist_ok=True)
    allb = b""
    files = {}
    for d in sorted(parts):
        body = b"".join(l + b"\n" for l in parts[d])
        allb += body
        fn = os.path.join(a.out, f"chain_listener_{d}.jsonl.gz")
        with open(fn, "wb") as fo:
            with gzip.GzipFile(filename="", mode="wb", fileobj=fo, mtime=0, compresslevel=6) as g:
                g.write(body)
        files[os.path.basename(fn)] = sha(open(fn, "rb").read())
    inj.update(original_file_sha256=sha(open(a.orig, "rb").read()), original_content_sha256=sha(raw),
               modified_content_sha256=sha(allb), partitions=files, n_records=allb.count(b"\n"))
    if a.manifest:
        json.dump(inj, open(a.manifest, "w"), indent=1, sort_keys=True)
    print(canon(inj))


if __name__ == "__main__":
    main()
