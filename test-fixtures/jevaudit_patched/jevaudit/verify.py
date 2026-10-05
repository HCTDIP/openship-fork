"""verify.py — 审计前置验证（0.2.0）。两条都过才允许出成绩单，任何一条不通过即拒审。

verify_ledger_integrity()    逐条核对三指纹：
    input_hash  ← 用账本里存的 input_payload 重新计算比对
    output_hash ← 用账本里存的 raw_response 重新计算比对
    code_hash   ← 必须登记在已知版本指纹表里（历史版本也算，只要登记过）
verify_causal_completeness() 检查 gate_evidence：四条规则齐全、字段齐全、passed 与 value/limit 自洽、
    ACT/VETO 与证据一致、audit_mode 合法、REPLAY 记录必须同时有原始调用时间和重审时间。
"""
import datetime
import hashlib
import json

from jevaudit.hctdip import ORDER
from jevaudit.ledger import input_hash as _jv_input_hash, output_hash as _jv_output_hash

EVIDENCE_FIELDS = ("rule", "check", "value", "limit", "passed", "ts")
AUDIT_MODES = ("LIVE", "REPLAY")


class AuditRejected(Exception):
    """前置验证不通过：拒审。.report 带完整失败明细。"""

    def __init__(self, report):
        self.report = report
        super().__init__(f"AUDIT REJECTED: integrity={report['integrity']['ok']} "
                         f"causal={report['causal']['ok']} "
                         f"failures={report['integrity']['n_failed'] + report['causal']['n_failed']}")


# ---- 指纹方案 -------------------------------------------------------------------------------
def _sha_r0(obj) -> str:
    """r0-sha256-v1：2026-09-29 第一批真实审计（run_audit.py）用的方案。完整 64 位。"""
    s = obj if isinstance(obj, (bytes, str)) else json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()


def _jv_input(payload):
    return _jv_input_hash(payload if isinstance(payload, str) else "",
                          payload if isinstance(payload, dict) else {"inputs": payload})


SCHEMES = {
    "r0-sha256-v1": {"input": _sha_r0, "output": _sha_r0},
    "jevaudit-canonical-v1": {"input": _jv_input, "output": _jv_output_hash},
}


def _rid(r, i):
    return r.get("id") if r.get("id") is not None else r.get("seq", i)


def _parse_ts(ts) -> bool:
    if not isinstance(ts, str) or not ts:
        return False
    try:
        datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


# ---- 1) 三指纹完整性 -------------------------------------------------------------------------
def verify_ledger_integrity(rows: list, known_code_hashes=None) -> dict:
    """逐条核对三指纹。known_code_hashes: {hash: 版本说明} 或 hash 集合；不给 → 全部判失败（fail-closed）。"""
    known = set(known_code_hashes or ())
    fails = []
    for i, r in enumerate(rows):
        rid = _rid(r, i)
        scheme = SCHEMES.get(r.get("fingerprint_scheme"))
        if scheme is None:
            fails.append({"id": rid, "field": "fingerprint_scheme", "reason": f"unknown scheme {r.get('fingerprint_scheme')!r}"})
            continue
        for fp, src in (("input_hash", "input_payload"), ("output_hash", "raw_response")):
            if not r.get(fp):
                fails.append({"id": rid, "field": fp, "reason": "missing"})
            elif src not in r:
                fails.append({"id": rid, "field": fp, "reason": f"{src} not stored, cannot recompute"})
            else:
                got = scheme["input" if fp == "input_hash" else "output"](r[src])
                if got != r[fp]:
                    fails.append({"id": rid, "field": fp, "reason": "mismatch", "stored": r[fp], "recomputed": got})
        ch = r.get("code_hash")
        if not ch or ch == "src-unavailable":
            fails.append({"id": rid, "field": "code_hash", "reason": "missing/unavailable"})
        elif not known:
            fails.append({"id": rid, "field": "code_hash", "reason": "no code-hash registry supplied (fail-closed)"})
        elif ch not in known:
            fails.append({"id": rid, "field": "code_hash", "reason": "not in registry", "stored": ch})
    bad_ids = {f["id"] for f in fails}
    return {"ok": not fails and bool(rows), "n": len(rows), "n_passed": len(rows) - len(bad_ids),
            "n_failed": len(bad_ids), "failures": fails}


# ---- 2) 因果记录完整性 -----------------------------------------------------------------------
def _expected_pass(e) -> bool:
    rule, v, lim = e["rule"], e["value"], e["limit"]
    if rule == "A1_DELAY" or rule == "A6_COMPUTE":
        return float(v) <= float(lim)
    if rule == "A4_NO_CAUSAL_TRACE":
        return v is True
    if rule == "BOUNDARY":
        return isinstance(v, dict) and v.get("boundary_violation") is False and v.get("decision_in_range") is True
    raise ValueError(rule)


def verify_causal_completeness(rows: list) -> dict:
    fails = []
    for i, r in enumerate(rows):
        rid = _rid(r, i)
        def bad(field, reason):
            fails.append({"id": rid, "field": field, "reason": reason})
        if r.get("audit_mode") not in AUDIT_MODES:
            bad("audit_mode", f"must be one of {AUDIT_MODES}, got {r.get('audit_mode')!r}")
        if r.get("audit_mode") == "REPLAY":
            if not _parse_ts(r.get("call_ts")):
                bad("call_ts", "REPLAY record needs original call time")
            if not _parse_ts(r.get("replay_ts")):
                bad("replay_ts", "REPLAY record needs replay time")
        ev = r.get("gate_evidence")
        if not isinstance(ev, list):
            bad("gate_evidence", "missing")
            continue
        rules = [e.get("rule") for e in ev if isinstance(e, dict)]
        if sorted(rules) != sorted(ORDER) or len(ev) != len(ORDER):
            bad("gate_evidence", f"rules must be exactly {list(ORDER)}, got {rules}")
            continue
        ok_items = True
        for e in ev:
            miss = [f for f in EVIDENCE_FIELDS if f not in e]
            if miss:
                bad(f"gate_evidence.{e['rule']}", f"missing fields {miss}"); ok_items = False; continue
            if not isinstance(e["passed"], bool):
                bad(f"gate_evidence.{e['rule']}", "passed must be bool"); ok_items = False
            if not _parse_ts(e["ts"]):
                bad(f"gate_evidence.{e['rule']}", "bad ts"); ok_items = False
            try:
                if _expected_pass(e) != e["passed"]:
                    bad(f"gate_evidence.{e['rule']}", f"passed={e['passed']} inconsistent with value={e['value']!r} limit={e['limit']!r}")
                    ok_items = False
            except (TypeError, ValueError):
                bad(f"gate_evidence.{e['rule']}", "value/limit not checkable"); ok_items = False
        if not ok_items:
            continue
        failed = [c for c in ORDER if not next(e for e in ev if e["rule"] == c)["passed"]]
        exp_dec = "VETO" if failed else "ACT"
        if r.get("gate_decision") != exp_dec:
            bad("gate_decision", f"{r.get('gate_decision')} but evidence says {exp_dec}")
        if r.get("gate_reason") != (failed[0] if failed else None):
            bad("gate_reason", f"{r.get('gate_reason')} but evidence says {failed[0] if failed else None}")
        if "gate_all_reasons" in r and list(r["gate_all_reasons"]) != failed:
            bad("gate_all_reasons", f"{r['gate_all_reasons']} but evidence says {failed}")
    bad_ids = {f["id"] for f in fails}
    return {"ok": not fails and bool(rows), "n": len(rows), "n_passed": len(rows) - len(bad_ids),
            "n_failed": len(bad_ids), "failures": fails}


def preflight(rows: list, known_code_hashes=None) -> dict:
    a = verify_ledger_integrity(rows, known_code_hashes)
    b = verify_causal_completeness(rows)
    return {"ok": a["ok"] and b["ok"], "integrity": a, "causal": b}


def require_verified(rows: list, known_code_hashes=None) -> dict:
    """审计入口：两条验证都过才返回；否则抛 AuditRejected（拒审）。"""
    rep = preflight(rows, known_code_hashes)
    if not rep["ok"]:
        raise AuditRejected(rep)
    return rep
