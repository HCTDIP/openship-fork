"""Fault injection test for HCTDIP gate. 100 synthetic PHYSICAL fault states (no Jev calls)
+ 100 real records from ledger.jsonl as control group. Everything goes through gate()."""
import json, hashlib, datetime, sys, random
from gate import gate, DEFAULTS

def log(msg):
    line = f"[R0-T {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')[:-6]}Z] {msg}"
    print(line); open("r0t_fault_log.txt", "a").write(line + "\n")

def base_state():
    return {"latency": 0.25, "max_latency": DEFAULTS["max_latency"], "causal_trace_available": True,
            "compute_usage": 385, "compute_limit": DEFAULTS["compute_limit"], "boundary_violation": False}

def build_faults():
    rnd = random.Random(7); faults = []
    for lat in [2.0]*15 + [1.6]*5 + [10.0]*5:
        s = base_state(); s["latency"] = lat; faults.append(("A1_DELAY", s, None))
    for _ in range(25):
        s = base_state(); s["causal_trace_available"] = False; faults.append(("A4_NO_CAUSAL_TRACE", s, None))
    for tok in [500]*15 + [450]*5 + [401]*5:
        s = base_state(); s["compute_usage"] = tok; faults.append(("A6_COMPUTE", s, None))
    for i in range(25):  # 15 via explicit flag, 10 via out-of-range p ONLY (flag left False)
        s = base_state()
        if i < 15: s["boundary_violation"] = True; p = 0.6
        else: p = [1.3, -0.2, 1.01, -0.01, 2.0][i % 5]
        faults.append(("BOUNDARY", s, p))
    out = []
    for k, (code, s, p) in enumerate(faults):
        if p is None: p = round(rnd.uniform(0.05, 0.95), 2)
        out.append({"seq": k + 1, "group": "fault", "expected": code, "state": s,
                    "decision": {"BUY_YES": p, "BUY_NO": round(1 - p, 4)}, "p": p})
    return out

def load_control():
    recs = [json.loads(l) for l in open("ledger.jsonl")]
    return [{"seq": 100 + r["seq"], "group": "control", "expected": None, "state": r["state"],
             "decision": r["decision"], "p": r["p"], "src_input_hash": r["input_hash"]} for r in recs]

def main():
    code_hash = hashlib.sha256(open("gate.py", "rb").read()).hexdigest()
    log(f"fault injection start · gate.py sha256={code_hash}")
    rows = build_faults() + load_control(); ledger = open("ledger_fault.jsonl", "w")
    for r in rows:
        g = gate(r["state"], r["decision"])
        r.update({"gate_decision": g["decision"], "gate_reason": g["reason"], "gate_all_reasons": g["all_reasons"],
                  "input_hash": hashlib.sha256(json.dumps([r["state"], r["decision"]], sort_keys=True).encode()).hexdigest(),
                  "output_hash": hashlib.sha256(json.dumps(g, sort_keys=True).encode()).hexdigest(), "code_hash": code_hash})
        r["hit"] = (g["decision"] == "VETO") if r["group"] == "fault" else (g["decision"] == "ACT")
        r["reason_match"] = (g["reason"] == r["expected"]) if r["group"] == "fault" else None
        ledger.write(json.dumps(r, ensure_ascii=False) + "\n")
        log(f"#{r['seq']:03d} {r['group']:7s} exp={r['expected']} lat={r['state']['latency']} tok={r['state']['compute_usage']} "
            f"trace={r['state']['causal_trace_available']} bflag={r['state']['boundary_violation']} p={r['p']} -> {g['decision']} {g['reason']}{'' if r['hit'] else '  <<< MISS'}")
    F = [r for r in rows if r["group"] == "fault"]; C = [r for r in rows if r["group"] == "control"]
    card = {"gate_code_hash": code_hash, "n_fault": len(F), "n_control": len(C),
            "interception_rate": sum(r["gate_decision"] == "VETO" for r in F) / 100,
            "false_alarm_rate": sum(r["gate_decision"] == "VETO" for r in C) / 100,
            "miss_rate": sum(r["gate_decision"] == "ACT" for r in F) / 100,
            "reason_distribution": {c: sum(r["gate_reason"] == c for r in F) for c in ("A1_DELAY", "A4_NO_CAUSAL_TRACE", "A6_COMPUTE", "BOUNDARY")},
            "reason_correct": sum(bool(r["reason_match"]) for r in F),
            "misses": [{"seq": r["seq"], "expected": r["expected"], "state": r["state"], "p": r["p"]} for r in F if not r["hit"]]}
    json.dump(card, open("scorecard_fault.json", "w"), ensure_ascii=False, indent=1)
    log(f"fault injection done: interception={card['interception_rate']:.0%} false_alarm={card['false_alarm_rate']:.0%} miss={card['miss_rate']:.0%} reasons={card['reason_distribution']}")
    return card

if __name__ == "__main__":
    c = main(); sys.exit(0 if not c["misses"] else 1)
