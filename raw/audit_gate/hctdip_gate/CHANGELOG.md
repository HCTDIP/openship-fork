# CHANGELOG — 所有修理留痕

## FIX-001 · 2026-09-29 · gate.py BOUNDARY 漏判 p 越界
- 发现：故障注入 #091–#100（10 条，p ∈ {1.3, -0.2, 1.01, -0.01, 2.0}，state.boundary_violation=False）→ 全部 ACT。拦截率 90%。
- 根源：gate.py 原第 19 行 `"BOUNDARY": bool(state.get("boundary_violation", False))` 只读 state 里的标志位，不看 decision 本身的取值范围。真实跑（run_audit.py build_state）是在闸门外面算好标志再传进去，所以真实 100 条没暴露。
- 修复：BOUNDARY = 标志位 OR decision 任一取值不在 [0,1]（含 NaN / 非数字 / 空 decision）。属于取值范围检查，不判断决策好坏。
- 验证：test_gate.py 新增 test_fix001_out_of_range_p_is_boundary；故障注入重跑。
- gate.py sha256 修复前：87faf4cc8d19fecd16a5bb3588fc3562dbc495c16175a9573cef678215dcf9ef
- gate.py sha256 修复后：见 git commit FIX-001 与 r0t_fault_log.txt 第二轮 start 行。

## FIX-002 — 2026-09-29 — A6 阈值 400 → 440（用户确认）
- 依据：100 条真实调用 input tokens min 372 / median 386 / p95 398 / max 400 / mean 386.0 / sd 5.9；
  400 = 正常最大值，2 条恰好等于 400，余量为 0。440 = max×1.1（约 mean+9sd）。
- 改动：gate.py `DEFAULTS["compute_limit"]` 400 → 440。判定逻辑未改。
- 影响：已落账记录的 state 自带 compute_limit=400，回放结论不变；新调用按 440。

## CHG-003 — 2026-09-29 — gate() 返回完整因果记录 gate_evidence（用户指令"A4 因果记录缺失修复"）
- 改动：gate() 每次返回 `gate_evidence`，4 条规则各一条 {rule, check, value, limit, passed, ts}，ACT 也全量记录。判定逻辑不变。
- 验证：test_gate.py 新增 test_chg003_gate_evidence、test_chg003_same_decisions_as_jevaudit（300 条已落账 state 与 jevaudit.hctdip 逐条一致）。
- commit 755a6ba；gate.py sha256 8cb55040de4bef7ec9dd19e54cc99fa4fea73b5685e52a0d29a8181fb5627bee

## FIX-003 — 2026-09-29 — 决策值为 bool 时放行
- 发现：CHG-003 一致性检查。gate.py 把 True/False 当 1/0（Python bool 是 int 子类），jevaudit.hctdip 判 BOUNDARY，两边不一致。
- 修复：gate.py 的越界判断加 `isinstance(v, bool)` → BOUNDARY。已落账 300 条无 bool 值，结论不变。
- 验证：test_fix003_bool_value_is_boundary。commit 755a6ba。

## MIG-001 — 2026-09-29 — [REPLAY AUDIT] 100 条真实记录迁移到 gate_evidence 新格式
- migrate_evidence.py：0 次 API 调用，audit_mode=REPLAY，每条同时记 call_ts（原始调用）和 replay_ts（重审）。
- 前置验证 verify_ledger_integrity 100/100、verify_causal_completeness 100/100；preflight_negative.py 6 种篡改 6/6 拒审。
- 结果：ACT 100 / VETO 0，与原始判定 0 条变化。输出 ledger_evidence.jsonl / scorecard_evidence.json / r0t_evidence_log.txt / report_evidence.md。
- 同时给旧产出补标签：scorecard.json=LIVE，scorecard_fault.json=FAULT_INJECTION，report_440.md / scorecard_440.json / ledger_440.jsonl=REPLAY。
- 代码版本指纹登记表 code_registry.json（gate.py 4 个版本 + jevaudit hctdip.py）。
