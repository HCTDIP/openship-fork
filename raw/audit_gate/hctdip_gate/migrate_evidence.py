"""[REPLAY AUDIT] 账本格式迁移：把 2026-09-29 那 100 条真实 Jev 调用迁移到带 gate_evidence 的新格式。
不调 API。state 用账本里存的 Jev 原始响应 + 当时实测延迟重建，闸门 = jevaudit.hctdip（A6=440）。
审计前先跑 verify_ledger_integrity + verify_causal_completeness，不通过即拒审（不出成绩单）。"""
import datetime, hashlib, json, sys, collections
sys.argv = [sys.argv[0]]
sys.path.insert(0, "/work/temp/pkg/jevaudit-0.2.0")
from jevaudit import hctdip
from jevaudit.verify import preflight, require_verified, AuditRejected
import run_audit as R  # 原始落账的指纹函数 / 输入构造（MODEL / Q / state_text）

TASK = "hctdip-ledger-evidence-migration"
ENGINE = "/work/temp/pkg/jevaudit-0.2.0/jevaudit/hctdip.py"
fsha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
LOG = open("r0t_evidence_log.txt", "w")
def L(m): LOG.write(f"[R0-T {now()}] {m}\n")

REG = json.load(open("code_registry.json"))
KNOWN = {h: v["label"] for h, v in REG.items()}
engine_hash = fsha(ENGINE)
assert engine_hash in KNOWN, "engine hctdip.py not registered"
assert fsha("gate.py") in KNOWN, "current gate.py not registered"

def klass(p, o):
    if p is None: return "error"
    if 0.3 < p < 0.7: return "ambiguous"
    return "good" if (p >= 0.7) == (o == 1) else "bad"

def main():
    pool = {m["condition_id"]: m for m in json.load(open("markets_pool.json"))}
    src = [json.loads(l) for l in open("ledger.jsonl")]
    L(f"START task={TASK} audit_mode=REPLAY src=ledger.jsonl n={len(src)} new_api_calls=0 "
      f"engine=jevaudit.hctdip sha256={engine_hash} DEFAULTS={hctdip.DEFAULTS}")
    replay_ts = now()
    out = []
    for r in src:
        m = pool[r["condition_id"]]
        payload = {"model": R.MODEL, "state": R.state_text(m), "questions": R.Q}
        resp = r["raw_response"]
        p = hctdip.extract_p(resp)
        st = hctdip.state_from_response(resp, r["state"]["latency"], p,
                                        boundary_violation=r["state"]["boundary_violation"])
        dec = {"BUY_YES": p, "BUY_NO": round(1 - p, 6)} if p is not None else {}
        g = hctdip.gate(st, dec, ts=replay_ts)
        rec = {
            "seq": r["seq"], "audit_mode": "REPLAY", "task": TASK,
            "call_ts": r["ts"], "replay_ts": replay_ts,
            "jev_id": r["jev_id"], "condition_id": r["condition_id"], "question": r["question"],
            "fingerprint_scheme": "r0-sha256-v1",
            "input_hash": r["input_hash"], "output_hash": r["output_hash"], "code_hash": engine_hash,
            "code_hash_origin": r["code_hash"],
            "input_payload": payload, "raw_response": resp,
            "state": st, "decision": dec, "p": p,
            "gate_decision": g["decision"], "gate_reason": g["reason"], "gate_all_reasons": g["all_reasons"],
            "confidence": g["confidence"], "gate_evidence": g["gate_evidence"],
            "gate_decision_origin": r["gate_decision"],
            "outcome": r["outcome"], "class": klass(p, r["outcome"]),
        }
        out.append(rec)
        ev = {e["rule"]: e for e in g["gate_evidence"]}
        L(f"#{r['seq']:03d} jev={r['jev_id']} call_ts={r['ts']} p={p} "
          f"BOUNDARY={'PASS' if ev['BOUNDARY']['passed'] else 'FAIL'} "
          f"A4={'PASS' if ev['A4_NO_CAUSAL_TRACE']['passed'] else 'FAIL'} "
          f"A1={ev['A1_DELAY']['value']}/{ev['A1_DELAY']['limit']}s {'PASS' if ev['A1_DELAY']['passed'] else 'FAIL'} "
          f"A6={ev['A6_COMPUTE']['value']:.0f}/{ev['A6_COMPUTE']['limit']:.0f} {'PASS' if ev['A6_COMPUTE']['passed'] else 'FAIL'} "
          f"-> {g['decision']} {g['reason']} (origin: {r['gate_decision']})")
    # 原始记录的判定脚本版本也必须在登记表里
    bad_origin = [x["seq"] for x in out if x["code_hash_origin"] not in KNOWN]
    assert not bad_origin, bad_origin

    # ---- 前置验证：不通过即拒审 ----
    rep = preflight(out, KNOWN)
    L(f"VERIFY verify_ledger_integrity ok={rep['integrity']['ok']} passed={rep['integrity']['n_passed']}/{rep['integrity']['n']}")
    L(f"VERIFY verify_causal_completeness ok={rep['causal']['ok']} passed={rep['causal']['n_passed']}/{rep['causal']['n']}")
    try:
        require_verified(out, KNOWN)
    except AuditRejected as e:
        L(f"REJECTED {e}"); LOG.close()
        json.dump(e.report, open("preflight_failures.json", "w"), ensure_ascii=False, indent=1)
        raise SystemExit(f"拒审：{e}")

    with open("ledger_evidence.jsonl", "w") as f:
        for x in out: f.write(json.dumps(x, ensure_ascii=False, allow_nan=False) + "\n")

    sc = hctdip.scorecard(out, known_code_hashes=KNOWN)
    C = collections.Counter(x["class"] for x in out)
    act = [x for x in out if x["gate_decision"] == "ACT"]; veto = [x for x in out if x["gate_decision"] == "VETO"]
    bad = [x for x in out if x["class"] == "bad"]
    evfail = {c: sum(not next(e for e in x["gate_evidence"] if e["rule"] == c)["passed"] for x in out) for c in hctdip.ORDER}
    card = {
        "audit_mode": "REPLAY", "label": "[REPLAY AUDIT] — not a live acceptance run",
        "task": TASK, "generated": now(), "replay_ts": replay_ts, "new_api_calls": 0,
        "call_ts_range": [min(x["call_ts"] for x in out), max(x["call_ts"] for x in out)],
        "engine": "jevaudit 0.2.0 / jevaudit.hctdip", "engine_sha256": engine_hash,
        "defaults": hctdip.DEFAULTS, "n": len(out),
        "preflight": {"integrity_ok": rep["integrity"]["ok"], "integrity_passed": rep["integrity"]["n_passed"],
                      "causal_ok": rep["causal"]["ok"], "causal_passed": rep["causal"]["n_passed"]},
        "act": sc["act"], "veto": sc["veto"],
        "veto_reason_first": sc["veto_reason_first"], "veto_reason_all": sc["veto_reason_all"],
        "evidence_rule_failures": evfail,
        "interception_rate": (sum(x["gate_decision"] == "VETO" for x in bad) / len(bad)) if bad else None,
        "false_alarm_rate": (sum(x["class"] == "good" for x in veto) / len(veto)) if veto else None,
        "miss_rate": (sum(x["class"] == "bad" for x in act) / len(act)) if act else None,
        "classes": dict(C),
        "absorbing": {"rejection": len(veto), "rollback": sum(x["class"] == "bad" for x in act),
                      "non_decisional": C.get("ambiguous", 0), "executed_ok": sum(x["class"] == "good" for x in act)},
        "changed_vs_origin": sum((x["gate_decision"]) != x["gate_decision_origin"] for x in out),
        "evidence_entries": sum(len(x["gate_evidence"]) for x in out),
    }
    json.dump(card, open("scorecard_evidence.json", "w"), ensure_ascii=False, indent=2)
    L(f"END act={card['act']} veto={card['veto']} changed_vs_origin={card['changed_vs_origin']} "
      f"evidence_entries={card['evidence_entries']} reasons_first={card['veto_reason_first']}")
    LOG.close()
    print(json.dumps(card, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
