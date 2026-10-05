"""python -m jevaudit.audit reconcile --ledger-glob 'ledgers/chain_listener_*.jsonl*' --wallets wallets.json \
       --known-code-hash <sha256> [--known-code-hash ...] --out-dir reconcile [--from N --to N] [--state state.json]

Writes <out-dir>/reconcile_<from>_<to>.json + .md and <out-dir>/cursor.json (last reconciled block),
so a daily run only checks blocks it has not checked before (manual --from/--to runs leave the cursor alone). Exit 0 = ran (findings are in the
report); exit 3 = findings and --fail-on-findings; exit 2 = bad input.
"""
import argparse, glob, gzip, json, os, sys, time

from jevaudit.audit.chain import DEFAULT_RPCS, check_partitions, reconcile_with_chain, verdicts, verify_ledger_integrity


def load(paths):
    out = []
    for p in paths:
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def md(rep, integ, files):
    c = rep["counts"]
    L = [f"# Reconcile {rep['range']['from']}–{rep['range']['to']} ({rep['range']['blocks']} blocks)", "",
         f"- run: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}, {rep['elapsed_s']} s",
         f"- ledger files: {', '.join(os.path.basename(f) for f in files)}",
         f"- nodes: {', '.join(rep['rpcs'])}", "",
         "| item | value |", "|---|---|",
         f"| ledger fills in range | {c['ledger_fills']} |",
         f"| chain fills (all) | {c['chain_fills_all']} |",
         f"| chain fills (tracked) | {c['chain_fills_tracked']} |",
         f"| matched | {c['matched']} |",
         f"| on chain, missing in ledger | {c['missing_in_ledger']} |",
         f"| in ledger, not on chain | {c['extra_in_ledger']} |",
         f"| input_hash mismatch | {c['input_hash_mismatch']} |",
         f"| integrity failures (output_hash / code_hash / duplicates) | {integ['n_failed']} |",
         f"| blocks with no node majority | {len(rep['unresolved_blocks'])} |",
         f"| chunks no node answered | {len(rep['unanswered_chunks'])} |",
         f"| empty blocks confirmed by ≥2 nodes | {len(rep['empty_blocks_confirmed'])} |",
         f"| node agreement sample | {rep['node_agreement_sample']['all_nodes_identical']}/{rep['node_agreement_sample']['blocks']} identical |",
         "", f"**Result: {'OK' if rep['ok'] and integ['ok'] else 'FINDINGS'}**", ""]
    L += ["## Verdicts", ""] + ([f"- {v['code']}: " + ", ".join(f"{k}={v[k]}" for k in v if k not in ("code", "fills") or (k == "fills" and isinstance(v[k], int))) for v in rep.get("verdicts", [])] or ["- PASS (no findings)"]) + [""]
    if rep["missing_blocks"]:
        L += ["## Blocks with missing fills", "", "| block | missing | in ledger |", "|---|---|---|"]
        L += [f"| {m['block']} | {m['missing_fills']} | {m['ledger_fills']} |" for m in rep["missing_blocks"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m jevaudit.audit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reconcile")
    r.add_argument("--ledger", nargs="*", default=[])
    r.add_argument("--ledger-glob", action="append", default=[])
    r.add_argument("--wallets", help="JSON list of tracked maker addresses; omit if the ledger tracks all fills")
    r.add_argument("--known-code-hash", action="append", default=[])
    r.add_argument("--rpc", action="append", default=[])
    r.add_argument("--out-dir", default="reconcile")
    r.add_argument("--from", dest="lo", type=int)
    r.add_argument("--to", dest="hi", type=int)
    r.add_argument("--state", help="listener state json: upper bound = last_processed (or next_block-1)")
    r.add_argument("--status", help="listener status json: run end time for the day-partition check when state has no updated_at")
    r.add_argument("--step", type=int, default=20)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--fail-on-findings", action="store_true")
    a = ap.parse_args(argv)

    files = sorted(set(a.ledger + [p for g in a.ledger_glob for p in glob.glob(g)]))
    if not files:
        print("no ledger files -> nothing to do"); return 0
    recs = load(files)
    if not recs:
        print("ledger files empty -> nothing to do"); return 0
    os.makedirs(a.out_dir, exist_ok=True)
    cur_p = os.path.join(a.out_dir, "cursor.json")
    lo = a.lo
    if lo is None:
        lo = json.load(open(cur_p))["last_block"] + 1 if os.path.exists(cur_p) else min(x["block"] for x in recs)
    hi = a.hi
    if hi is None and a.state:
        s = json.load(open(a.state))
        hi = s.get("last_processed", s["next_block"] - 1)
    if hi is None:
        hi = max(x["block"] for x in recs)
    if lo > hi:
        print(f"nothing new to reconcile (from {lo} > to {hi})"); return 0
    wallets = json.load(open(a.wallets)) if a.wallets else None
    in_range = [x for x in recs if lo <= x["block"] <= hi]
    integ = verify_ledger_integrity(in_range, a.known_code_hash)
    rep = reconcile_with_chain(recs, a.rpc or DEFAULT_RPCS, lo, hi, wallets, step=a.step, workers=a.workers,
                               progress=lambda m: print(m, flush=True))
    rep["integrity"] = integ
    miss_parts = []
    if a.state:
        s = json.load(open(a.state))
        end = s.get("updated_at") or (json.load(open(a.status)).get("ts") if a.status else None)
        if s.get("start_ts") and end:
            miss_parts = check_partitions(files, s["start_ts"], end)
    rep["missing_partitions"] = miss_parts
    rep["verdicts"] = verdicts(rep, integ, recs, miss_parts)
    rep["ok"] = rep["ok"] and not rep["verdicts"]
    rep["ledger_files"] = [os.path.basename(f) for f in files]
    base = os.path.join(a.out_dir, f"reconcile_{lo}_{hi}")
    with open(base + ".json", "w") as f:
        json.dump(rep, f, indent=1, sort_keys=True)
    with open(base + ".md", "w") as f:
        f.write(md(rep, integ, files))
    if a.lo is None and a.hi is None and not rep["unanswered_chunks"]:
        # cursor only for incremental runs, and only when every chunk was actually read
        with open(cur_p, "w") as f:
            json.dump({"last_block": hi, "updated": time.time(), "report": os.path.basename(base) + ".json"}, f)
    print(open(base + ".md").read())
    findings = not (rep["ok"] and integ["ok"])
    return 3 if (findings and a.fail_on_findings) else 0


if __name__ == "__main__":
    sys.exit(main())
