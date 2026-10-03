"""用 jevaudit.hctdip（HCTDIP 融入 Jev 审计包的版本）在 A6=440 下重审 2026-09-29 那 100 条真实 Jev 调用。
不发新 API 请求：state 由账本里保存的 Jev 原始响应 + 当时实测延迟重建。"""
import hashlib, json, sys, datetime, collections
sys.path.insert(0, "/work/temp/pkg/jevaudit-0.2.0")
from jevaudit import hctdip
from run_audit import sha  # 与原始落账同一个指纹函数

SRC = "ledger.jsonl"
HC = "/work/temp/pkg/jevaudit-0.2.0/jevaudit/hctdip.py"
code_hash = hashlib.sha256(open(HC, "rb").read()).hexdigest()
now = lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
log = open("r0t_440_log.txt", "w")
def L(m): log.write(f"[{now()}] {m}\n")

def klass(p, o):
    if p is None: return "error"
    if 0.3 < p < 0.7: return "ambiguous"
    return "good" if (p >= 0.7) == (o == 1) else "bad"

rows = [json.loads(l) for l in open(SRC)]
assert hctdip.DEFAULTS["compute_limit"] == 440
L(f"R0-T START task=hctdip-jev-replay-440 src={SRC} n={len(rows)} hctdip.py sha256={code_hash} DEFAULTS={hctdip.DEFAULTS}")
out, changed = [], 0
for r in rows:
    resp = r["raw_response"]
    # 输出指纹校验：账本里的原始响应没被改过
    assert sha(resp) == r["output_hash"], f"#{r['seq']} raw_response 与 output_hash 不符"
    p = hctdip.extract_p(resp)
    st = hctdip.state_from_response(resp, r["state"]["latency"], p,
                                    boundary_violation=r["state"]["boundary_violation"])
    dec = {"BUY_YES": p, "BUY_NO": round(1 - p, 6)} if p is not None else {}
    g = hctdip.gate(st, dec)
    same = (g["decision"], g["reason"]) == (r["gate_decision"], r["gate_reason"])
    changed += not same
    rec = {"seq": r["seq"], "jev_id": r["jev_id"], "condition_id": r["condition_id"], "question": r["question"],
           "input_hash": r["input_hash"], "output_hash": r["output_hash"], "code_hash": code_hash,
           "code_hash_v400": r["code_hash"], "state": st, "decision": dec, "p": p,
           "gate_decision": g["decision"], "gate_reason": g["reason"], "gate_all_reasons": g["all_reasons"],
           "gate_decision_v400": r["gate_decision"], "outcome": r["outcome"], "class": klass(p, r["outcome"]),
           "a6_headroom_tokens": st["compute_limit"] - st["compute_usage"],
           "a1_headroom_s": round(st["max_latency"] - st["latency"], 4)}
    out.append(rec)
    L(f"#{r['seq']:03d} jev={r['jev_id']} p={p} lat={st['latency']}s tok={st['compute_usage']}/{st['compute_limit']} "
      f"-> {g['decision']} {g['reason']} (v400: {r['gate_decision']}) class={rec['class']}")
with open("ledger_440.jsonl", "w") as f:
    for rec in out: f.write(json.dumps(rec, ensure_ascii=False) + "\n")

C = collections.Counter(x["class"] for x in out)
act = [x for x in out if x["gate_decision"] == "ACT"]; veto = [x for x in out if x["gate_decision"] == "VETO"]
bad = [x for x in out if x["class"] == "bad"]; good = [x for x in out if x["class"] == "good"]
first = {c: sum(x["gate_reason"] == c for x in out) for c in hctdip.ORDER}
allh = {c: sum(c in x["gate_all_reasons"] for x in out) for c in hctdip.ORDER}
tok = sorted(x["state"]["compute_usage"] for x in out); lat = sorted(x["state"]["latency"] for x in out)
card = {
    "task": "hctdip-jev-replay-440", "generated": now(), "engine": "jevaudit 0.2.0 / jevaudit.hctdip",
    "hctdip_py_sha256": code_hash, "defaults": hctdip.DEFAULTS, "n": len(out), "new_api_calls": 0,
    "classes": dict(C), "act": len(act), "veto": len(veto),
    "interception_rate": (sum(x["gate_decision"] == "VETO" for x in bad) / len(bad)) if bad else None,
    "false_alarm_rate": (sum(x["class"] == "good" for x in veto) / len(veto)) if veto else None,
    "miss_rate": (sum(x["class"] == "bad" for x in act) / len(act)) if act else None,
    "veto_reason_first": first, "veto_reason_all": allh,
    "absorbing": {"rejection": len(veto), "rollback": sum(x["class"] == "bad" for x in act),
                  "non_decisional": C.get("ambiguous", 0), "executed_ok": sum(x["class"] == "good" for x in act)},
    "changed_vs_400": changed,
    "a6": {"limit": 440, "tokens_min": tok[0], "tokens_median": tok[50], "tokens_max": tok[-1],
           "min_headroom": 440 - tok[-1], "at_or_above_400": sum(t >= 400 for t in tok)},
    "a1": {"limit_s": 1.5, "latency_min_s": lat[0], "latency_median_s": lat[50], "latency_max_s": lat[-1],
           "min_headroom_s": round(1.5 - lat[-1], 4)},
    "boundary_violations": sum(x["state"]["boundary_violation"] for x in out),
    "causal_trace_missing": sum(not x["state"]["causal_trace_available"] for x in out),
}
json.dump(card, open("scorecard_440.json", "w"), ensure_ascii=False, indent=2)
L(f"R0-T CHECK output_hash 100/100 与账本原始响应一致")
L(f"R0-T END act={len(act)} veto={len(veto)} changed_vs_400={changed} reasons_first={first}")
log.close()
print(json.dumps(card, ensure_ascii=False, indent=1))
