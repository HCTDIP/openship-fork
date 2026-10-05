"""0.2.0：gate_evidence + 两条前置验证 + physical_gate 合并。"""
import copy, json, os
import pytest
from jevaudit import (hctdip, hctdip_gate as gate, audited_call, load_ledger, log_action, require_task_env,
                      verify_ledger_integrity, verify_causal_completeness, require_verified, AuditRejected)

OK = {"latency": 0.25, "max_latency": 1.5, "causal_trace_available": True,
      "compute_usage": 386, "compute_limit": 440, "boundary_violation": False}
D = {"BUY_YES": 0.72, "BUY_NO": 0.28}
def s(**kw): return {**OK, **kw}
def _resp(p=0.8, tok=386):
    return {"id": "dec_x", "usage": {"input_tokens": tok}, "answers": {"resolve_yes": {"noul": p}}}

# ---- gate_evidence ----
def test_evidence_all_four_rules_on_act():
    g = gate(OK, D)
    assert [e["rule"] for e in g["gate_evidence"]] == list(hctdip.ORDER)
    for e in g["gate_evidence"]:
        assert set(e) == {"rule", "check", "value", "limit", "passed", "ts"} and e["passed"] is True

@pytest.mark.parametrize("state,rule", [(s(latency=2.0), "A1_DELAY"), (s(compute_usage=441), "A6_COMPUTE"),
    (s(causal_trace_available=False), "A4_NO_CAUSAL_TRACE"), (s(boundary_violation=True), "BOUNDARY")])
def test_evidence_marks_failed_rule(state, rule):
    g = gate(state, D)
    failed = [e["rule"] for e in g["gate_evidence"] if not e["passed"]]
    assert failed == [rule] and g["reason"] == rule

def test_evidence_values_and_limits():
    ev = {e["rule"]: e for e in gate(s(latency=1.6, compute_usage=450), D)["gate_evidence"]}
    assert ev["A1_DELAY"]["value"] == 1.6 and ev["A1_DELAY"]["limit"] == 1.5
    assert ev["A6_COMPUTE"]["value"] == 450 and ev["A6_COMPUTE"]["limit"] == 440

def test_evidence_nan_decision_serializable():
    g = gate(OK, {"BUY_YES": float("nan")})
    json.dumps(g, allow_nan=False)  # 证据里不能留 NaN（JSONL 要可解析）
    assert g["reason"] == "BOUNDARY"

# ---- audited_call → 新格式账本 → 两条验证 ----
def _live_ledger(tmp_path, n=3):
    lp = str(tmp_path / "l.jsonl")
    for i in range(n):
        audited_call(lambda: _resp(), ledger_path=lp, record_id=f"t{i}", inputs={"q": i})
    return load_ledger(lp)

def test_live_record_passes_both(tmp_path):
    rows = _live_ledger(tmp_path)
    reg = {rows[0]["code_hash"]: "test"}
    assert verify_ledger_integrity(rows, reg)["ok"]
    assert verify_causal_completeness(rows)["ok"]
    assert rows[0]["audit_mode"] == "LIVE" and rows[0]["call_ts"] and rows[0]["gate_evidence"]
    assert hctdip.scorecard(rows, known_code_hashes=reg)["verified"] is True

def test_no_registry_fails_closed(tmp_path):
    rows = _live_ledger(tmp_path, 1)
    r = verify_ledger_integrity(rows, None)
    assert not r["ok"] and "fail-closed" in r["failures"][0]["reason"]
    with pytest.raises(AuditRejected):
        hctdip.scorecard(rows)

@pytest.mark.parametrize("mut,field", [
    (lambda r: r["raw_response"]["answers"]["resolve_yes"].update(noul=0.1), "output_hash"),
    (lambda r: r["input_payload"].update(q=999), "input_hash"),
    (lambda r: r.update(code_hash="src-deadbeef0000"), "code_hash"),
    (lambda r: r.pop("raw_response"), "output_hash"),
    (lambda r: r.update(fingerprint_scheme="md5"), "fingerprint_scheme")])
def test_integrity_detects_tampering(tmp_path, mut, field):
    rows = _live_ledger(tmp_path, 1); reg = {rows[0]["code_hash"]}
    mut(rows[0])
    r = verify_ledger_integrity(rows, reg)
    assert not r["ok"] and r["failures"][0]["field"] == field
    with pytest.raises(AuditRejected):
        require_verified(rows, reg)

@pytest.mark.parametrize("mut", [
    lambda r: r.pop("gate_evidence"),
    lambda r: r["gate_evidence"].pop(),                                  # 少一条规则
    lambda r: r["gate_evidence"][2].pop("limit"),                         # 少字段
    lambda r: r["gate_evidence"][2].update(value=9.9),                    # passed 与数值矛盾
    lambda r: r["gate_evidence"][3].update(passed="yes"),
    lambda r: r["gate_evidence"][0].update(ts="yesterday"),
    lambda r: r.update(gate_decision="VETO"),                             # 决策与证据不符
    lambda r: r.update(gate_reason="A1_DELAY"),
    lambda r: r.update(audit_mode="MAYBE"),
    lambda r: r.update(audit_mode="REPLAY"),                              # REPLAY 缺 call_ts/replay_ts
])
def test_causal_detects_gaps(tmp_path, mut):
    rows = _live_ledger(tmp_path, 1); reg = {rows[0]["code_hash"]}
    mut(rows[0])
    if rows[0].get("audit_mode") == "REPLAY": rows[0].pop("replay_ts", None)
    assert not verify_causal_completeness(rows)["ok"]
    with pytest.raises(AuditRejected):
        require_verified(rows, reg)

def test_empty_ledger_rejected():
    with pytest.raises(AuditRejected):
        require_verified([], {"x"})

def test_exception_call_record_still_complete(tmp_path):
    lp = str(tmp_path / "e.jsonl")
    def boom(): raise RuntimeError("HTTP 500")
    audited_call(boom, ledger_path=lp, record_id="e1", inputs={"q": 1})
    rows = load_ledger(lp)
    assert rows[0]["gate_decision"] == "VETO" and verify_causal_completeness(rows)["ok"]

# ---- physical_gate（0.1.1 合并） ----
def test_log_action_writes_evidence(tmp_path):
    g = gate(s(latency=3), D)
    assert log_action("T1", "A1", "hctdip:VETO", log_dir=str(tmp_path), evidence=g)
    line = json.loads(open(tmp_path / "T1_A1.log").read().strip())
    assert line["gate"]["reason"] == "A1_DELAY" and len(line["gate"]["gate_evidence"]) == 4

def test_log_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("R0T_LOG_DIR", str(tmp_path / "envdir"))
    assert log_action("T", "A", "x")
    assert (tmp_path / "envdir" / "T_A.log").exists()

def test_log_action_unwritable_returns_false():
    assert log_action("T", "A", "x", log_dir="/proc/nope/cannot") is False

def test_audited_call_logs_via_physical_gate(tmp_path):
    audited_call(lambda: _resp(tok=999), task_id="T9", agent_id="A9", log_dir=str(tmp_path), record_id="r")
    line = json.loads(open(tmp_path / "T9_A9.log").read().strip())
    assert line["gate"]["decision"] == "VETO" and line["gate"]["reason"] == "A6_COMPUTE"

def test_require_task_env_fail_closed(monkeypatch):
    monkeypatch.delenv("R0T_TASK_ID", raising=False); monkeypatch.delenv("R0T_AGENT_ID", raising=False)
    with pytest.raises(SystemExit) as e:
        require_task_env()
    assert e.value.code == 2
    monkeypatch.setenv("R0T_TASK_ID", "t"); monkeypatch.setenv("R0T_AGENT_ID", "a")
    assert require_task_env() == ("t", "a")

# ---- 真实数据回归：2026-09-29 100 条 [REPLAY AUDIT] 新格式账本 ----
def test_real_evidence_ledger_passes_preflight():
    d = os.path.join(os.path.dirname(__file__), "data")
    rows = load_ledger(os.path.join(d, "ledger_evidence.jsonl"))
    reg = json.load(open(os.path.join(d, "code_registry.json")))
    assert len(rows) == 100 and all(r["audit_mode"] == "REPLAY" for r in rows)
    rep = require_verified(rows, reg)
    assert rep["integrity"]["n_passed"] == 100 and rep["causal"]["n_passed"] == 100
    for r in rows:  # 当前引擎重判必须与证据一致
        g = gate(r["state"], r["decision"])
        assert [e["passed"] for e in g["gate_evidence"]] == [e["passed"] for e in r["gate_evidence"]]
