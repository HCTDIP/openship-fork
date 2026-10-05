"""hctdip.py — HCTDIP 物理闸门（physical safety gate）。

分工（两层，互不替代）：
    物理层  hctdip.gate()   —— 系统在物理上正不正常：延迟 / 因果链 / 算力 / 越界 → ACT | VETO
    置信层  gating.gate2()  —— 按 p 分档 KEEP / CONFIRM / DROP

hctdip.gate() 只看物理状态和决策值的格式/范围，不评判决策内容本身。
推荐执行顺序：先过 hctdip.gate()，ACT 之后才交给 gate2()。

理由码：
    BOUNDARY            state["boundary_violation"] 为真，或决策值非数字 / NaN / 越出 [0,1] / 空决策
    A4_NO_CAUSAL_TRACE  causal_trace_available 为假（缺响应 id / usage / p）
    A1_DELAY            latency > max_latency
    A6_COMPUTE          compute_usage > compute_limit
检查顺序 BOUNDARY → A4 → A1 → A6，第一个命中的作为 reason，all_reasons 列出全部命中项。
"""
import os
import time

# A6 校准（FIX-002, 2026-09-29, 用户确认）：100 条真实调用 input tokens max=400 / mean=386 / sd=5.9，
# 400 余量为 0 → 取 440（max×1.1）。max_latency 仍为占位值。
DEFAULTS = {
    "max_latency": 1.5,     # 秒
    "compute_limit": 440,   # 每次调用的 input tokens（原 400）
}

ORDER = ("BOUNDARY", "A4_NO_CAUSAL_TRACE", "A1_DELAY", "A6_COMPUTE")
REASONS = ORDER


def _out_of_range(decision) -> bool:
    if not decision:
        return True
    for v in decision.values():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return True
        if v != v or not (0.0 <= v <= 1.0):  # NaN 或越界
            return True
    return False


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def gate(state: dict, decision: dict, *, ts: str = None) -> dict:
    """物理闸门。

    state = {latency, max_latency, causal_trace_available, compute_usage, compute_limit, boundary_violation}
    decision = 模型输出，例如 {"BUY_YES": 0.72, "BUY_NO": 0.28}
    返回 {"decision": "ACT"|"VETO", "reason": None|理由码, "confidence": float, "all_reasons": [...],
          "gate_evidence": [ {rule, check, value, limit, passed, ts} × 4 ]}

    gate_evidence（0.2.0 新增，因果记录）：四条规则按 ORDER 各一条，无论 ACT 还是 VETO 都全量记录，
    事后可以逐条复核"为什么放行 / 为什么拦下"。ts = 本次闸门判定时间（UTC）。
    """
    ts = ts or _now()
    decision = decision or {}
    oor = _out_of_range(decision)
    nums = [v for v in decision.values() if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v]
    conf = max(nums) if nums else 0.0
    lat = float(state.get("latency", 0.0))
    max_lat = float(state.get("max_latency", DEFAULTS["max_latency"]))
    use = float(state.get("compute_usage", 0.0))
    lim = float(state.get("compute_limit", DEFAULTS["compute_limit"]))
    bflag = bool(state.get("boundary_violation", False))
    trace = bool(state.get("causal_trace_available", False))
    checks = {
        "BOUNDARY": bflag or oor,
        "A4_NO_CAUSAL_TRACE": not trace,
        "A1_DELAY": lat > max_lat,
        "A6_COMPUTE": use > lim,
    }
    evidence = [
        {"rule": "BOUNDARY", "check": "boundary_violation == false AND decision values numeric in [0,1]",
         "value": {"boundary_violation": bflag, "decision_in_range": not oor,
                   "decision": {k: (v if isinstance(v, (int, float, type(None))) and v == v else repr(v))
                                for k, v in decision.items()}},
         "limit": {"boundary_violation": False, "decision_range": [0.0, 1.0]},
         "passed": not checks["BOUNDARY"], "ts": ts},
        {"rule": "A4_NO_CAUSAL_TRACE", "check": "causal_trace_available == true",
         "value": trace, "limit": True, "passed": not checks["A4_NO_CAUSAL_TRACE"], "ts": ts},
        {"rule": "A1_DELAY", "check": "latency <= max_latency (s)",
         "value": lat, "limit": max_lat, "passed": not checks["A1_DELAY"], "ts": ts},
        {"rule": "A6_COMPUTE", "check": "compute_usage <= compute_limit (input tokens)",
         "value": use, "limit": lim, "passed": not checks["A6_COMPUTE"], "ts": ts},
    ]
    hits = [c for c in ORDER if checks[c]]
    return {
        "decision": "VETO" if hits else "ACT",
        "reason": hits[0] if hits else None,
        "confidence": round(float(conf), 4),
        "all_reasons": hits,
        "gate_evidence": evidence,
    }


def extract_p(resp: dict, name: str = "resolve_yes"):
    """从 OpenRouter Decisions (Jev) 响应里取 noul 概率；取不到返回 None。"""
    try:
        return float(resp["answers"][name]["noul"])
    except (KeyError, TypeError, ValueError):
        return None


def state_from_response(resp: dict, latency: float, p=None, *, max_latency: float = None,
                        compute_limit: float = None, boundary_violation: bool = False) -> dict:
    """由一次真实调用的响应 + 实测延迟构造 gate() 的 state。

    compute_usage 取 usage.input_tokens（请求规模，不是模型内部算力）。
    boundary_violation 可由调用方追加业务越界（如决策时间晚于市场截止）；p 越界由 gate() 自己判。
    """
    resp = resp or {}
    usage = resp.get("usage") or {}
    return {
        "latency": round(float(latency), 4),
        "max_latency": DEFAULTS["max_latency"] if max_latency is None else max_latency,
        "causal_trace_available": bool(resp.get("id") and usage and p is not None),
        "compute_usage": usage.get("input_tokens", 0),
        "compute_limit": DEFAULTS["compute_limit"] if compute_limit is None else compute_limit,
        "boundary_violation": bool(boundary_violation),
    }


def audited_call(call, *, name: str = "resolve_yes", ledger_path: str = None, record_id: str = None,
                 inputs=None, code_path: str = None, boundary_violation: bool = False,
                 task_id: str = None, agent_id: str = None, log_dir: str = None, **limits) -> dict:
    """调用一次模型 → 实测延迟 → 物理闸门 → （可选）写三指纹账本。

    call: 无参函数，返回原始响应 dict，例如
          lambda: jevkit.Client().decide(questions, state=text)
    调用抛异常 → 无决策值 + 无因果记录 → 必然 VETO（reason=BOUNDARY，all_reasons 含 A4_NO_CAUSAL_TRACE），不会放行。
    """
    from jevaudit.ledger import add_record, code_hash, input_hash, output_hash

    call_ts = _now()
    t0 = time.monotonic()
    error = None
    try:
        resp = call()
    except Exception as e:  # 调用失败按"无因果记录"处理，必然 VETO
        resp, error = {}, f"{type(e).__name__}: {e}"[:300]
    latency = time.monotonic() - t0
    p = extract_p(resp, name)
    state = state_from_response(resp, latency, p, boundary_violation=boundary_violation, **limits)
    decision = {} if p is None else {"BUY_YES": p, "BUY_NO": round(1 - p, 6)}
    g = gate(state, decision)
    if log_dir is not None or task_id:
        from jevaudit.physical_gate import log_action
        log_action(task_id or "untasked", agent_id or "unknown", f"hctdip:{g['decision']}:{record_id}",
                   log_dir=log_dir, evidence=g)
    ch, ch_src = code_hash(code_path), (code_path or "sys.argv[0]")
    if ch == "src-unavailable":
        ch, ch_src = code_hash(os.path.abspath(__file__)), "jevaudit/hctdip.py"
    rec = {
        "id": record_id,
        "input_hash": _input_fp(inputs),
        "output_hash": output_hash(resp),
        "code_hash": ch,
        "code_hash_source": ch_src,
        "p": p,
        "state": state,
        "gate_decision": g["decision"],
        "gate_reason": g["reason"],
        "gate_all_reasons": g["all_reasons"],
        "gate_evidence": g["gate_evidence"],
        "audit_mode": "LIVE",
        "fingerprint_scheme": "jevaudit-canonical-v1",
        "input_payload": inputs,
        "raw_response": resp,
        "error": error,
        "outcome": None,  # 到期回填 1/0
        "call_ts": call_ts,
        "ts": g["gate_evidence"][0]["ts"],
    }
    if ledger_path:
        add_record(ledger_path, rec)
    return {"gate": g, "p": p, "response": resp, "record": rec}


def _input_fp(inputs) -> str:
    from jevaudit.ledger import input_hash
    return input_hash(inputs if isinstance(inputs, str) else "", inputs if isinstance(inputs, dict) else {"inputs": inputs})


def scorecard(rows: list, *, known_code_hashes=None, verify: bool = True) -> dict:
    """闸门成绩单：ACT/VETO 计数 + 理由码分布（首因 / 全部命中）。rows 为账本记录。

    0.2.0：出成绩单前先跑 verify_ledger_integrity + verify_causal_completeness，
    任何一条不通过抛 AuditRejected（拒审）。verify=False 仅供调试，报告里会写明未验证。
    """
    if verify:
        from jevaudit.verify import require_verified
        require_verified(rows, known_code_hashes=known_code_hashes)
    first = {c: 0 for c in ORDER}
    anyhit = {c: 0 for c in ORDER}
    act = veto = 0
    for r in rows:
        if r.get("gate_decision") == "VETO":
            veto += 1
            if r.get("gate_reason") in first:
                first[r["gate_reason"]] += 1
        else:
            act += 1
        for c in r.get("gate_all_reasons") or []:
            if c in anyhit:
                anyhit[c] += 1
    modes = {}
    for r in rows:
        m = r.get("audit_mode", "UNKNOWN")
        modes[m] = modes.get(m, 0) + 1
    return {"n": len(rows), "act": act, "veto": veto,
            "veto_reason_first": first, "veto_reason_all": anyhit,
            "audit_modes": modes, "verified": bool(verify)}
