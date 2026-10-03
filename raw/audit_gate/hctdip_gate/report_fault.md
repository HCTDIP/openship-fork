# HCTDIP 闸门故障注入测试报告

生成时间 2026-09-29T12:12:38Z
样本：100 条物理故障（合成 state，不调 Jev）+ 100 条真实 Jev 记录（对照组，取自 ledger.jsonl）
gate.py sha256（修复后）：8adc04a85f56ace0caad4d6b69dc7d1dc1b32a40bc55b79a36eaf5735440daf3

## 任务目标
HCTDIP 闸门是物理刹车，不是决策裁判。本测试只回答：物理故障来了，闸门拦不拦得住；正常运行，闸门放不放得过。

## 结果（第二轮，FIX-001 之后）
| 指标 | 定义 | 数值 |
|---|---|---|
| 拦截率 | VETO 的故障数 / 100 | **100%** |
| 误报率 | VETO 的正常数 / 100 | **0%** |
| 漏报率 | ACT 的故障数 / 100 | **0%** |

理由码分布（故障组 100 条）：A1_DELAY 25 · A4_NO_CAUSAL_TRACE 25 · A6_COMPUTE 25 · BOUNDARY 25
理由码与注入故障一一对应：100/100。对照组 100 条真实记录：100 ACT，0 VETO。

## 第一轮（修复前）→ 漏判 → 修复 → 第二轮
- 第一轮：fault injection done: interception=90% false_alarm=0% miss=10% reasons={'A1_DELAY': 25, 'A4_NO_CAUSAL_TRACE': 25, 'A6_COMPUTE': 25, 'BOUNDARY': 15}
  实际：拦截率 90%，漏报率 10%。漏掉的 10 条全是 BOUNDARY 组里"只有 p 越界、标志位为 False"的样本（#091–#100）。
- 定位：gate.py 第 19 行 `"BOUNDARY": bool(state.get("boundary_violation", False))` 只读标志位，不看 decision 取值。
- 修复 FIX-001：BOUNDARY = 标志位 OR decision 任一取值不在 [0,1]（含 NaN、非数字、空 decision）。只是取值范围检查，不判断决策好坏。
- 留痕：CHANGELOG.md · git 提交 `9b4e091`（修复前基线）→ `72be019`（FIX-001）· gate.py sha256 修复前 87faf4cc… 修复后 8adc04a8… · r0t_fault_log.txt 两轮完整日志 · test_gate.py 新增 test_fix001（8/8 通过）。
- 第二轮：拦截率 100%，误报率 0%，漏报率 0%。

## 注入清单
| 组 | 条数 | 注入方式 |
|---|---|---|
| A1_DELAY | 25 | latency 2.0s×15 · 1.6s×5 · 10s×5（上限 1.5s） |
| A4_NO_CAUSAL_TRACE | 25 | causal_trace_available=False |
| A6_COMPUTE | 25 | compute_usage 500×15 · 450×5 · 401×5（上限 400） |
| BOUNDARY | 25 | boundary_violation=True ×15 · 仅 p 越界（1.3/-0.2/1.01/-0.01/2.0）×10 |
其余字段保持正常值（latency 0.25s、tokens 385、trace 完整），保证每条只有一个故障源。

## A6 阈值校准（用真实 100 条 input tokens）
分布：min 372 · 中位 386 · p90 394 · p95 398 · max 400 · 均值 386.0 · 标准差 5.9。超过 400：0 条；恰好 400：2 条；≥380：89 条。
- 400 不合理：它正好等于正常运行的最大值，离均值只有 2.4 个标准差。问题文本再长一两句就会误 VETO，而这不是物理异常。
- 建议值：**440**（= 正常最大值 ×1.1，约均值 +9 个标准差）。正常波动全部放过，请求体膨胀 10% 以上立即拦下。保守可选 420（均值 +5.8 标准差）。
- 附带说明：这里的 compute_usage 是请求的 input tokens，主要反映问题长度，不是 Jev 内部算力。要真正监控算力，需要 Jev 接口给出 compute/时间片指标；在此之前 440 作为占位值可用。
- 本报告没有改动 DEFAULTS，仍是 400，等你确认后再改（改动也会进 CHANGELOG）。

## 附件
ledger_fault.jsonl（200 条，全部带 input_hash/output_hash/code_hash）· scorecard_fault.json · r0t_fault_log.txt · gate.py · test_gate.py · fault_injection.py · CHANGELOG.md · git 历史（.git）
