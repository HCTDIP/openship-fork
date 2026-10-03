# [REPLAY AUDIT] HCTDIP 闸门 · 因果记录（gate_evidence）账本迁移报告

> **audit_mode: REPLAY** — 不是 live 验收。0 次新 API 调用。
> 源数据：2026-09-29 10:56Z 的 100 条真实 Jev 调用（typesafe/jev-1.13）。重审时间 2026-09-29 15:14Z。

## 任务目标
HCTDIP 不判断 Jev 准不准，只判断 Jev 的决策**在物理上安不安全**（延迟 / 因果链 / 算力 / 越界）。
本次只做一件事：把账本升级成每条 ACT/VETO 都带完整因果记录的新格式，并在出成绩单前强制验证。

## 1. 新账本格式（ledger_evidence.jsonl，100 条）
每条记录新增：

| 字段 | 含义 |
|---|---|
| `audit_mode` | `REPLAY`（本批）/ `LIVE`（以后新批次） |
| `call_ts` / `replay_ts` | 原始 Jev 调用时间 / 本次重审时间 |
| `gate_evidence` | 4 条规则各一条：`rule` · `check` · `value` · `limit` · `passed` · `ts` |
| `input_payload` / `raw_response` | 原始输入与原始响应，用于重新计算指纹 |
| `fingerprint_scheme` | `r0-sha256-v1`（与原始落账同一套指纹函数） |
| `code_hash` / `code_hash_origin` | 本次判定代码（jevaudit hctdip.py）/ 原始判定代码（gate.py 基线版） |

ACT 也记全部 4 条证据——"为什么放行"和"为什么拦下"一样可查。共 400 条证据项。

## 2. 审计前置验证（不通过即拒审）

| 验证 | 结果 |
|---|---|
| `verify_ledger_integrity()` input_hash 重算比对 | 100/100 一致 |
| `verify_ledger_integrity()` output_hash 重算比对 | 100/100 一致 |
| `verify_ledger_integrity()` code_hash 在登记表内 | 100/100（原始版本也在表内） |
| `verify_causal_completeness()` 4 规则齐全 · 字段齐全 · passed 与数值自洽 · 决策与证据一致 · REPLAY 双时间戳 | 100/100 |

**拒审对照**（preflight_negative.json）：对第 37 条做 6 种篡改，6/6 被拒审：

| 篡改 | 结果 |
|---|---|
| T1 改原始响应里的 p | 拒审（output_hash 不符） |
| T2 改输入文本 | 拒审（input_hash 不符） |
| T3 换成未登记的判定代码 | 拒审（code_hash 不在登记表） |
| T4 删掉 gate_evidence | 拒审（缺证据） |
| T5 证据写延迟超限但决策仍 ACT | 拒审（决策与证据矛盾） |
| T6 REPLAY 记录缺原始调用时间 | 拒审（缺 call_ts） |

## 3. 代码版本指纹登记表（code_registry.json）

| sha256（前 12 位） | 版本 |
|---|---|
| 87faf4cc8d19 | gate.py 基线（FIX-001 前，原始 100 条的判定代码） |
| 8adc04a85f56 | gate.py FIX-001 |
| f4ddffdcc2cd | gate.py FIX-002（A6=440） |
| 8cb55040de4b | gate.py CHG-003 gate_evidence + FIX-003 |
| 9a938ded8b71 | jevaudit 0.2.0 hctdip.py（本次判定引擎） |

## 4. 闸门结果（A6=440，max_latency=1.5s）

| 指标 | 结果 |
|---|---|
| ACT / VETO | 100 / 0 |
| 与原始判定相比有变化 | 0 条 |
| VETO 理由码分布 A1 / A4 / A6 / BOUNDARY | 0 / 0 / 0 / 0 |
| 证据项中未通过的规则 | 0 / 400 |
| 拦截率 | 0 / 5 = 0%（5 条事后不利的决策物理状态全部正常） |
| 误报率 | 无 VETO，不适用 |
| 漏报率 | 5 / 100 = 5%（均为物理安全、结果不利，非闸门职责范围） |
| 吸收态 rejection / rollback / non-decisional / executed_ok | 0 / 5 / 53 / 42 |

物理故障的拦截能力由故障注入验证（report_fault.md）：100 条故障 100% 拦截，四类理由码各 25 条，对照组 0 误拦。

## 5. 本轮修理留痕

| 编号 | 文件 | 内容 | git |
|---|---|---|---|
| CHG-003 | gate.py | 每次判定返回 gate_evidence，判定逻辑不变 | 755a6ba |
| FIX-003 | gate.py | 决策值为 True/False 时原来会当作 1/0 放行，现判 BOUNDARY（与 jevaudit 一致；由 300 条一致性检查发现） | 755a6ba |
| 0.2.0 | jevaudit | gate_evidence；合并 0.1.1 physical_gate（日志目录可配置 + 落证据）；采用 0.1.1 code_hash；新增两条验证函数；成绩单未验证即拒审 | ee4cef4 |

## 6. 附件
ledger_evidence.jsonl · scorecard_evidence.json · r0t_evidence_log.txt（R0-T 日志，每条 4 规则逐项 PASS/FAIL）·
preflight_negative.json · code_registry.json · migrate_evidence.py · preflight_negative.py · gate.py · test_gate.py

## 下一步
停止对这 100 条做重审。下一批新市场、新决策用 `audited_call()` 直接落 LIVE 新格式（需一次性 key）。
