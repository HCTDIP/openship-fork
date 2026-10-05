# Changelog

## Unreleased（仅 GitHub，未发 PyPI，版本号不变）— 2026-10-05
- **新增** `jevaudit/audit/`：`verify_ledger_integrity()`（链上成交账本三指纹）、`reconcile_with_chain()`（3 个独立 RPC 逐笔对账）、
  CLI `python -m jevaudit.audit reconcile`（增量游标 `cursor.json`，输出 json + md）。只用标准库。
  节点答复里出现请求范围以外的区块 → 整次答复作废（2026-10-03 事故的实际表现）。
- **改动** `pyproject.toml` packages.find 加 `jevaudit.*`，否则子包 `jevaudit.audit` 装不进去。
- **新增** `tests/test_audit_chain.py`（7 个离线测试，mock RPC）。
- **新增** `CASES.md` + `cases/2026-10-03-tip-race/missing_fills.csv`（61 笔，sha256 7e3f2074）。
- 未改：`hctdip` / `gating` / `verify` / 闸门参数（A1 1.5s、A6 440、BOUNDARY）。
- 已知：`tests/test_hctdip.py::test_code_hash_default_fixed` 在 `pytest` 入口下本来就失败（main 上未改动时同样失败），本次未处理。

## 0.2.0 — 2026-09-29
- **新增** 因果记录：`hctdip.gate()` 每次返回 `gate_evidence`（4 条规则各一条：rule / check / value / limit / passed / ts），
  ACT 也全量记录；`audited_call()` 账本新增 `gate_evidence`、`audit_mode`（LIVE/REPLAY）、`call_ts`、`input_payload`、
  `raw_response`、`fingerprint_scheme`、`code_hash_source`。
- **新增** `jevaudit.verify`：`verify_ledger_integrity()`（input/output_hash 用存下来的原始数据重算比对，code_hash 必须在已知版本登记表里，
  不给登记表 = fail-closed）、`verify_causal_completeness()`（证据齐全且自洽、决策与证据一致、REPLAY 必须带双时间戳）、
  `preflight()`、`require_verified()` / `AuditRejected`。`hctdip.scorecard()` 默认先验证，不通过即拒审。
- **合并** 0.1.1 的 `physical_gate`（`require_task_env` 原样保留 fail-closed exit 2）；`log_action()` 日志目录可配置
  （参数 > `R0T_LOG_DIR` > 原默认 `/var/minis/shared/logs/r0t`），新增 `evidence=` 把闸门因果记录一起落盘；
  `audited_call(task_id=, agent_id=, log_dir=)` 自动落 R0-T 日志。
- **采用** 0.1.1 的 `code_hash()` 语义（默认 `sys.argv[0]`，定位不到返回 src-unavailable）；`audited_call` 遇 unavailable 改哈希 hctdip.py 并记来源。
- **新增** `scripts/prepublish_check.py`：没有 GitHub 源码地址 / 未确认轮换 PyPI token / 包内含密钥或测试文件 → 拒绝发布。
- **测试数据** `tests/data/ledger_evidence.jsonl`（100 条真实记录 [REPLAY AUDIT] 新格式）+ `code_registry.json`。
- **新增** `jevaudit.hctdip`：HCTDIP 物理闸门 `gate(state, decision)` → ACT/VETO + 理由码
  (BOUNDARY / A4_NO_CAUSAL_TRACE / A1_DELAY / A6_COMPUTE)，含 FIX-001（决策值越界/NaN/非数字/空 → BOUNDARY）。
  另有 `state_from_response`、`extract_p`、`audited_call`（调用→测延迟→闸门→三指纹账本）、`scorecard`。
- **修复** `code_hash()` 默认值：0.1.0 去找不存在的 `mm_poc.py`，永远返回 `src-unavailable`；
  现在默认哈希当前主脚本，交互环境退回哈希 hctdip.py + gating.py。
- **改动** 测试移到 `tests/`，不再打进发行包；附带 200 条故障注入账本 + 100 条真实 Jev 账本做回归。
- 未改：`gate2` / `accuracy_report` / `price_sem` / 账本读写逻辑。
- A6 `compute_limit` 默认 400 → 440（FIX-002：实测 max=400、mean=386、sd=5.9，400 余量为 0）。

## 0.1.1 — 2026-09-29（PyPI，用户代理发布）
- code_hash 默认取 sys.argv[0]；新增 physical_gate（require_task_env / log_action）。0.2.0 已全部并入。

## 0.1.0 — 2026-09-29
- 首发（PyPI 原版，本仓库首个提交原样导入）。
