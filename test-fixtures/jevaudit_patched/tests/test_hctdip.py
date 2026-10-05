"""HCTDIP 物理闸门回归测试。tests/ 不打进发行包。"""
import json, math, os
import pytest
from jevaudit import hctdip, hctdip_gate as gate, audited_call, load_ledger, code_hash

OK = {"latency": 0.25, "max_latency": 1.5, "causal_trace_available": True,
      "compute_usage": 386, "compute_limit": 400, "boundary_violation": False}
D = {"BUY_YES": 0.72, "BUY_NO": 0.28}

def s(**kw): return {**OK, **kw}

def test_normal_act():
    g = gate(OK, D); assert g["decision"] == "ACT" and g["reason"] is None and g["confidence"] == 0.72

@pytest.mark.parametrize("state,code", [
    (s(latency=2.0), "A1_DELAY"), (s(latency=1.6), "A1_DELAY"), (s(latency=10), "A1_DELAY"),
    (s(causal_trace_available=False), "A4_NO_CAUSAL_TRACE"),
    (s(compute_usage=401), "A6_COMPUTE"), (s(compute_usage=500), "A6_COMPUTE"),
    (s(boundary_violation=True), "BOUNDARY")])
def test_each_reason(state, code):
    g = gate(state, D); assert g["decision"] == "VETO" and g["reason"] == code

def test_boundaries_are_strict():  # 等于上限不算超
    assert gate(s(latency=1.5, compute_usage=400), D)["decision"] == "ACT"

@pytest.mark.parametrize("dec", [{"BUY_YES": 1.3}, {"BUY_YES": -0.2}, {"BUY_YES": 1.01},
    {"BUY_YES": -0.01}, {"BUY_YES": 2.0}, {"BUY_YES": math.nan}, {"BUY_YES": "0.5"},
    {"BUY_YES": True}, {}, None])
def test_fix001_decision_value_boundary(dec):
    g = gate(OK, dec); assert g["decision"] == "VETO" and g["reason"] == "BOUNDARY"

def test_order_and_all_reasons():
    g = gate(s(latency=9, compute_usage=999, causal_trace_available=False, boundary_violation=True), D)
    assert g["reason"] == "BOUNDARY" and g["all_reasons"] == list(hctdip.ORDER)

def test_replay_recorded_fault_injection():
    rows = load_ledger(os.path.join(os.path.dirname(__file__), "data", "ledger_fault.jsonl"))
    assert len(rows) == 200
    for r in rows:
        g = gate(r["state"], r["decision"])
        assert g["decision"] == r["gate_decision"] and g["reason"] == r["gate_reason"], r["seq"]
    faults = [r for r in rows if r["group"] == "fault"]
    assert all(gate(r["state"], r["decision"])["reason"] == r["expected"] for r in faults)
    assert all(gate(r["state"], r["decision"])["decision"] == "ACT" for r in rows if r["group"] != "fault")

def _resp(p=0.8, tok=386):
    return {"id": "dec_x", "usage": {"input_tokens": tok}, "answers": {"resolve_yes": {"noul": p}}}

def test_audited_call_act_and_ledger(tmp_path):
    lp = str(tmp_path / "l.jsonl")
    out = audited_call(lambda: _resp(), ledger_path=lp, record_id="t1", inputs={"q": 1})
    assert out["gate"]["decision"] == "ACT" and out["p"] == 0.8
    rec = load_ledger(lp)[0]
    for k in ("input_hash", "output_hash", "code_hash"): assert rec[k] and rec[k] != "src-unavailable"

def test_audited_call_exception_is_veto():
    def boom(): raise RuntimeError("Jev HTTP 500")
    out = audited_call(boom)
    # 调用失败 → 无决策值（FIX-001: 空决策 = BOUNDARY，排序第一）且无因果记录，两个理由都要命中
    assert out["gate"]["decision"] == "VETO"
    assert out["gate"]["all_reasons"][:2] == ["BOUNDARY", "A4_NO_CAUSAL_TRACE"]
    assert "Jev HTTP 500" in out["record"]["error"]

def test_audited_call_compute_and_p_range():
    assert audited_call(lambda: _resp(tok=450))["gate"]["reason"] == "A6_COMPUTE"
    assert audited_call(lambda: _resp(p=1.4))["gate"]["reason"] == "BOUNDARY"

def test_code_hash_default_fixed():
    assert code_hash() != "src-unavailable"

def test_scorecard():
    rows = [{"gate_decision": "VETO", "gate_reason": "A1_DELAY", "gate_all_reasons": ["A1_DELAY", "A6_COMPUTE"]},
            {"gate_decision": "ACT", "gate_reason": None, "gate_all_reasons": []}]
    c = hctdip.scorecard(rows, verify=False)  # 0.2.0: 手搓行没有指纹/证据，默认会拒审
    assert c["act"] == 1 and c["veto"] == 1 and c["veto_reason_first"]["A1_DELAY"] == 1 and c["veto_reason_all"]["A6_COMPUTE"] == 1

def test_replay_real_jev_ledger():
    """2026-09-29 100 条真实 Jev 调用（typesafe/jev-1.13），闸门结论必须逐条复现。"""
    rows = load_ledger(os.path.join(os.path.dirname(__file__), "data", "ledger_real.jsonl"))
    assert len(rows) == 100
    for r in rows:
        g = gate(r["state"], r["decision"])
        assert (g["decision"], g["reason"]) == (r["gate_decision"], r["gate_reason"]), r["seq"]

def test_fix002_a6_default_440():
    st = {k: v for k, v in OK.items() if k != "compute_limit"}
    assert gate({**st, "compute_usage": 440}, D)["decision"] == "ACT"
    assert gate({**st, "compute_usage": 441}, D)["reason"] == "A6_COMPUTE"
    assert hctdip.DEFAULTS["compute_limit"] == 440
