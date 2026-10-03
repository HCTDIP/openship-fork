from gate import gate
OK = dict(latency=0.5, max_latency=1.5, causal_trace_available=True, compute_usage=100, compute_limit=400, boundary_violation=False)
D = {"BUY_YES": 0.72, "BUY_NO": 0.28}
def t(**kw): return gate({**OK, **kw}, D)
def test_act(): r = t(); assert r["decision"] == "ACT" and r["reason"] is None and r["confidence"] == 0.72
def test_a1(): assert t(latency=2.0)["reason"] == "A1_DELAY"
def test_a4(): assert t(causal_trace_available=False)["reason"] == "A4_NO_CAUSAL_TRACE"
def test_a6(): assert t(compute_usage=500)["reason"] == "A6_COMPUTE"
def test_boundary(): assert t(boundary_violation=True)["reason"] == "BOUNDARY"
def test_priority(): r = t(boundary_violation=True, latency=9); assert r["reason"] == "BOUNDARY" and r["all_reasons"] == ["BOUNDARY", "A1_DELAY"]
def test_empty_decision(): assert gate(OK, {})["confidence"] == 0.0


def test_fix001_out_of_range_p_is_boundary():
    """FIX-001: out-of-range decision values must VETO even when the state flag is False."""
    s = {"latency": 0.2, "max_latency": 1.5, "causal_trace_available": True,
         "compute_usage": 300, "compute_limit": 400, "boundary_violation": False}
    for p in (1.3, -0.2, 1.01, -0.01, 2.0, float("nan")):
        g = gate(s, {"BUY_YES": p, "BUY_NO": 1 - p})
        assert g["decision"] == "VETO" and g["reason"] == "BOUNDARY", p
    assert gate(s, {})["reason"] == "BOUNDARY"
    assert gate(s, {"BUY_YES": 0.0, "BUY_NO": 1.0})["decision"] == "ACT"


def test_chg003_gate_evidence():
    """CHG-003: every gate() result carries gate_evidence for all four rules."""
    for kw, failed in [({}, []), ({"latency": 2.0}, ["A1_DELAY"]), ({"compute_usage": 500}, ["A6_COMPUTE"]),
                       ({"causal_trace_available": False}, ["A4_NO_CAUSAL_TRACE"]), ({"boundary_violation": True}, ["BOUNDARY"])]:
        g = t(**kw)
        ev = g["gate_evidence"]
        assert [e["rule"] for e in ev] == ["BOUNDARY", "A4_NO_CAUSAL_TRACE", "A1_DELAY", "A6_COMPUTE"]
        assert all(set(e) == {"rule", "check", "value", "limit", "passed", "ts"} for e in ev)
        assert [e["rule"] for e in ev if not e["passed"]] == failed == g["all_reasons"]


def test_chg003_same_decisions_as_jevaudit():
    """gate.py and jevaudit.hctdip must agree on every recorded state (300 rows)."""
    import json, sys
    sys.path.insert(0, "/work/temp/pkg/jevaudit-0.2.0")
    from jevaudit.hctdip import gate as jg
    rows = [json.loads(l) for f in ("ledger.jsonl", "ledger_fault.jsonl") for l in open(f)]
    assert len(rows) == 300
    for r in rows:
        a, b = gate(r["state"], r["decision"]), jg(r["state"], r["decision"])
        assert (a["decision"], a["reason"], a["all_reasons"]) == (b["decision"], b["reason"], b["all_reasons"])
        assert [e["passed"] for e in a["gate_evidence"]] == [e["passed"] for e in b["gate_evidence"]]


def test_fix003_bool_value_is_boundary():
    for v in (True, False):
        assert t.__globals__["gate"](OK, {"BUY_YES": v})["reason"] == "BOUNDARY"
    import sys; sys.path.insert(0, "/work/temp/pkg/jevaudit-0.2.0")
    from jevaudit.hctdip import gate as jg
    assert jg(OK, {"BUY_YES": True})["reason"] == "BOUNDARY"
