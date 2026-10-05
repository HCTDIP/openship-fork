"""Compare one reconcile report with what the injector did. Prints one JSON line; exit 0 = as expected.
Observed codes come from report['verdicts'] when the reconciler emits them; otherwise (stock jevaudit
404c1fb, which has no verdict codes) they are derived from its counters, and codes it cannot produce are absent."""
import glob, json, sys

test, rep_dir, man_p = sys.argv[1], sys.argv[2], sys.argv[3]
reps = sorted(glob.glob(f"{rep_dir}/reconcile_*.json"))
if not reps:
    print(json.dumps({"test": test, "pass": False, "actual": "NO REPORT"})); sys.exit(1)
rep = json.load(open(reps[-1]))
man = json.load(open(man_p))
integ = rep.get("integrity", {})
if "verdicts" in rep:
    codes = [v["code"] for v in rep["verdicts"]]
    src = "verdicts"
else:
    src = "derived-from-counters"
    codes = []
    if rep["counts"]["extra_in_ledger"]: codes.append("EXTRA_IN_LEDGER")
    if rep["counts"]["missing_in_ledger"]: codes.append("MISSING_FROM_LEDGER")
    if rep["counts"]["input_hash_mismatch"]: codes.append("INPUT_HASH_MISMATCH")
    if rep["unresolved_blocks"]: codes.append("UNRESOLVED_BLOCK")
    if rep["unanswered_chunks"]: codes.append("UNANSWERED_CHUNK")
    if not integ.get("ok", True): codes.append("INTEGRITY_FAIL")
mb = {m["block"]: m["missing_fills"] for m in rep["missing_blocks"]}
mf = {(m["block"], m["tx"].lower(), m["log_index"]) for m in rep["missing_fills"]}
ex = {(e["block"], e["tx"].lower(), e["log_index"]) for e in rep["extra_fills"]}
v = rep.get("verdicts", [])
ok, why = False, ""
if test == "T1":
    f = man["forged"]
    ok = set(codes) == {"EXTRA_IN_LEDGER"} and ex == {(f["block"], f["tx"].lower(), f["log_index"])}
    expect = f"EXTRA_IN_LEDGER block {f['block']} tx {f['tx'][:12]}… only"
elif test == "T2":
    d = man["deleted"]
    ok = set(codes) == {"MISSING_FROM_LEDGER"} and mf == {(d["block"], d["tx"].lower(), d["log_index"])}
    expect = f"MISSING_FROM_LEDGER block {d['block']} tx {d['tx'][:12]}… li {d['log_index']} only"
elif test == "T3":
    d = man["deleted_block"]
    hit = [x for x in v if x["code"] == "MISSING_BLOCK" and x.get("block") == d["block"] and x.get("fills") == d["fills"]]
    loc = mb == {d["block"]: d["fills"]}
    ok = loc and (bool(hit) if src == "verdicts" else True) and set(codes) <= {"MISSING_FROM_LEDGER", "MISSING_BLOCK"}
    expect = f"block {d['block']} missing {d['fills']} fills (located), nothing else"
elif test == "T35":
    d = man["deleted_tail"]
    hit = [x for x in v if x["code"] == "TRAILING_GAP" and x.get("from") == d["from"] and x.get("to") == d["to"] and x.get("fills") == d["fills"]]
    ok = bool(hit) and set(mb) == set(d["blocks"]) and sum(mb.values()) == d["fills"]
    expect = f"TRAILING_GAP {d['from']}-{d['to']} ({d['fills']} fills)"
elif test == "T36":
    d = man["deleted_partition"]
    hit = [x for x in v if x["code"] == "MISSING_DAY_PARTITION" and x.get("file") == d["file"]]
    ok = bool(hit) and sum(mb.values()) == d["fills"]
    expect = f"MISSING_DAY_PARTITION {d['file']} ({d['fills']} fills)"
elif test == "T5":
    ok = codes == [] and rep["ok"] and integ.get("ok") and not rep["unresolved_blocks"] and not rep["unanswered_chunks"]
    expect = "PASS, no gap, no error"
actual = {"codes": codes, "source": src, "missing_blocks": mb, "missing_n": rep["counts"]["missing_in_ledger"],
          "extra_n": rep["counts"]["extra_in_ledger"], "hash_mismatch": rep["counts"]["input_hash_mismatch"],
          "unresolved": len(rep["unresolved_blocks"]), "unanswered": len(rep["unanswered_chunks"]),
          "integrity_failed": integ.get("n_failed"), "verdicts": v, "range": rep["range"]}
print(json.dumps({"test": test, "pass": bool(ok), "expected": expect, "actual": actual}, sort_keys=True))
sys.exit(0 if ok else 1)
