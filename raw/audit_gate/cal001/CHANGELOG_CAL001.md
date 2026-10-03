## CAL-001 — 2026-09-30 — 跟单管线独立参数档（gate.py 代码未改）
- 发现：Jev 默认参数（A1 1.5 秒 / A6 440 token）套在跟单管线上，60,000 条决策 100% 被 A1 拦下——跟单的"延迟"是信号年龄（分钟级），不是 API 响应时间（秒级）；LightGBM 没有 token，A6 单位不适用。
- 处理：不改 gate.py 判定逻辑，只新增参数档 hctdip_copy_params.json（A1 60 秒、A6 5 毫秒、BOUNDARY 三条可执行性检查），通过 state 传入。Jev 参数档保持 1.5 秒 / 440 不变。
- gate.py sha256 与 CHG-003/FIX-003 版本一致（见 run_log.txt）。
- 验证：ledger_copy_backtest.jsonl（60,000 行 × 2 套参数，audit_mode=BACKTEST，三指纹 + gate_evidence）；scorecard_copy.json。
- 风险：阈值在同一验证集上选出，属样本内校准；上线前需用 10 月新数据前向确认。
