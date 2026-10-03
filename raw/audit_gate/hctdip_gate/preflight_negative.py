"""拒审对照：对新账本做 6 种篡改/缺失，每种都必须被两条前置验证拦下（拒审）。不调 API。"""
import copy, json, sys, datetime
sys.path.insert(0, "/work/temp/pkg/jevaudit-0.2.0")
from jevaudit.verify import require_verified, AuditRejected
KNOWN = {h: v["label"] for h, v in json.load(open("code_registry.json")).items()}
rows = [json.loads(l) for l in open("ledger_evidence.jsonl")]
now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
CASES = {
    "T1 改原始响应里的 p（output_hash）": lambda r: r["raw_response"]["answers"]["resolve_yes"].update(noul=0.99),
    "T2 改输入文本（input_hash）": lambda r: r["input_payload"].update(state=r["input_payload"]["state"] + " "),
    "T3 未登记的判定代码（code_hash）": lambda r: r.update(code_hash="0" * 64),
    "T4 删掉 gate_evidence": lambda r: r.pop("gate_evidence"),
    "T5 证据说延迟超限但仍 ACT": lambda r: r["gate_evidence"][2].update(value=9.9, passed=False),
    "T6 REPLAY 缺原始调用时间": lambda r: r.pop("call_ts"),
}
log = open("r0t_evidence_log.txt", "a")
require_verified(rows, KNOWN)
log.write(f"[R0-T {now()}] NEGATIVE-CONTROL baseline ledger_evidence.jsonl -> PASS\n")
res = {}
for name, mut in CASES.items():
    t = copy.deepcopy(rows); mut(t[37])
    try:
        require_verified(t, KNOWN); res[name] = "NOT REJECTED"
    except AuditRejected as e:
        f = (e.report["integrity"]["failures"] + e.report["causal"]["failures"])[0]
        res[name] = f"REJECTED ({f['field']}: {f['reason']})"
    log.write(f"[R0-T {now()}] NEGATIVE-CONTROL {name} -> {res[name]}\n")
log.close()
json.dump(res, open("preflight_negative.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1))
assert all(v.startswith("REJECTED") for v in res.values())
