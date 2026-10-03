"""HCTDIP gate: decide whether a Jev decision may be executed (ACT) or must be vetoed (VETO)."""

# Placeholder parameters. Replace them once calibrated.
DEFAULTS = {
    "max_latency": 1.5,      # seconds
    "compute_limit": 440,    # input tokens per call (FIX-002: A6 calibrated, was 400)
}

# Checks run in this order; the first failure wins.
ORDER = ("BOUNDARY", "A4_NO_CAUSAL_TRACE", "A1_DELAY", "A6_COMPUTE")


def gate(state: dict, decision: dict, ts: str = None) -> dict:
    """state: latency/max_latency/causal_trace_available/compute_usage/compute_limit/boundary_violation.
    decision: Jev output, e.g. {"BUY_YES": 0.72, "BUY_NO": 0.28}."""
    decision = decision or {}
    conf = max(decision.values()) if decision else 0.0
    # FIX-001 (2026-09-29, fault injection #091-#100): BOUNDARY used to read only state["boundary_violation"].
    # An out-of-range p in the decision itself (e.g. 1.3 / -0.2) passed as ACT. Now the gate also checks
    # the decision values directly. This is a range/format check, not a judgement of the decision's quality.
    # FIX-003 (2026-09-29): a bool value (True/False) counted as 1/0 and passed; jevaudit.hctdip already
    # treats bool as BOUNDARY. Aligned gate.py with it (found by the parity check in CHG-003).
    out_of_range = (not decision) or any(
        isinstance(v, bool) or (not isinstance(v, (int, float))) or v != v or not (0.0 <= v <= 1.0) for v in decision.values())
    checks = {
        "BOUNDARY": bool(state.get("boundary_violation", False)) or out_of_range,
        "A4_NO_CAUSAL_TRACE": not state.get("causal_trace_available", False),
        "A1_DELAY": state.get("latency", 0.0) > state.get("max_latency", DEFAULTS["max_latency"]),
        "A6_COMPUTE": state.get("compute_usage", 0.0) > state.get("compute_limit", DEFAULTS["compute_limit"]),
    }
    # CHG-003 (2026-09-29): every call returns gate_evidence — one entry per rule (ACT and VETO alike):
    # rule / check / value / limit / passed / ts. Decision logic above is unchanged.
    import datetime
    ts = ts or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    clean = {k: (v if isinstance(v, (int, float)) and v == v else repr(v)) for k, v in decision.items()}
    evidence = [
        {"rule": "BOUNDARY", "check": "boundary_violation == false AND decision values numeric in [0,1]",
         "value": {"boundary_violation": bool(state.get("boundary_violation", False)),
                   "decision_in_range": not out_of_range, "decision": clean},
         "limit": {"boundary_violation": False, "decision_range": [0.0, 1.0]},
         "passed": not checks["BOUNDARY"], "ts": ts},
        {"rule": "A4_NO_CAUSAL_TRACE", "check": "causal_trace_available == true",
         "value": bool(state.get("causal_trace_available", False)), "limit": True,
         "passed": not checks["A4_NO_CAUSAL_TRACE"], "ts": ts},
        {"rule": "A1_DELAY", "check": "latency <= max_latency (s)",
         "value": float(state.get("latency", 0.0)), "limit": float(state.get("max_latency", DEFAULTS["max_latency"])),
         "passed": not checks["A1_DELAY"], "ts": ts},
        {"rule": "A6_COMPUTE", "check": "compute_usage <= compute_limit (input tokens)",
         "value": float(state.get("compute_usage", 0.0)), "limit": float(state.get("compute_limit", DEFAULTS["compute_limit"])),
         "passed": not checks["A6_COMPUTE"], "ts": ts},
    ]
    hits = [c for c in ORDER if checks[c]]
    return {"decision": "VETO" if hits else "ACT", "reason": hits[0] if hits else None,
            "confidence": round(conf, 4), "all_reasons": hits, "gate_evidence": evidence}
