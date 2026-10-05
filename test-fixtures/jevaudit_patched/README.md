# jevaudit — 通用校准审计器

> Audit ANY decision model, not just Jev.
> decide → HCTDIP 物理闸门 → 三指纹账本 → Brier + 基线 + 校准曲线报告。

## Why

任何"模型给概率、事后有真值"的决策系统都需要审计：置信度 p 和真实结果 o
到底差多少？是不是瞎猜？基线是多少？jevaudit 把审计变成三件套：
**账本（可回溯）+ Brier（可比较）+ 校准曲线（可诊断）**。

- **三指纹账本**：input_hash / output_hash / code_hash——输入给偏了、模型漂移了、
  判定脚本改了，首查指纹
- **基线内置**：全猜 0.5 的 Brier = **0.25 常数**（与结果分布无关），模型低于
  0.25 才是有用——报告自动对比，难听话自动打（>0.4 直接写"接近随机"）
- **前视防护**：审计时只看决策时点可得的信息，结算后的信息严禁进输入

## Install

```bash
pip install jevaudit
```

## Quickstart

```python
from jevaudit import add_record, load_ledger, input_hash, output_hash, code_hash
from jevaudit import gate2, brier_score, calibration_curve, report

# 1) 每次决策记一条（三指纹 + check_spec 必填）
add_record("ledger.jsonl", {
    "id": "dec-001",
    "input_hash": input_hash(state, questions),   # sha256(规范输入)[:32]
    "output_hash": output_hash(response),         # sha256(响应)[:32]
    "code_hash": code_hash(),                     # 默认哈希当前主脚本
    "p": 0.95,                                    # 模型给的置信度
    "outcome": 1,                                 # 真实回填结果 (1/0)
    "check_spec": {"baseline": "implied_price", "tick": 0.01},  # 用的什么基准
})

# 2) 审计：Brier + 基线 + 校准曲线
rows = load_ledger("ledger.jsonl")
b = brier_score([r["p"] for r in rows], [r["outcome"] for r in rows])
curve = calibration_curve([r["p"] for r in rows], [r["outcome"] for r in rows])
report(rows, "calibration_report.md")   # 产出 Markdown（含 0.25 基线对比行）

# 3) 门控：KEEP / CONFIRM / DROP
action = gate2(0.75)   # KEEP (>=0.7) / CONFIRM (0.3-0.7) / DROP (<0.3)
```

## HCTDIP 物理闸门（0.2.0 新增）

两层闸门，分工不同，不互相替代：

| 层 | 函数 | 判什么 | 输出 |
|---|---|---|---|
| 物理层 | `hctdip.gate(state, decision)` | 延迟 / 因果链 / 算力 / 越界——系统在物理上安不安全 | ACT / VETO + 理由码 |
| 置信层 | `gate2(p)` | 按 p 分档 | KEEP / CONFIRM / DROP |

先过物理层，ACT 之后才进置信层。物理层不评判决策内容。

| 理由码 | 条件 |
|---|---|
| `BOUNDARY` | `boundary_violation` 为真，或决策值非数字 / NaN / 越出 [0,1] / 空 |
| `A4_NO_CAUSAL_TRACE` | 缺响应 id / usage / p |
| `A1_DELAY` | `latency > max_latency`（默认 1.5s） |
| `A6_COMPUTE` | `compute_usage > compute_limit`（默认 440 input tokens，按 100 条真实调用校准） |

```python
from jevkit import Client
from jevaudit import audited_call, hctdip

c = Client()  # 读 OPENROUTER_API_KEY
q = {"resolve_yes": {"type": "noul", "instructions": "Will X happen?",
                     "criteria": {"true": "X happens", "false": "X does not"}}}
out = audited_call(lambda: c.decide(q, state="..."), ledger_path="ledger.jsonl",
                   record_id="dec-001", inputs=q)
if out["gate"]["decision"] == "ACT":
    ...  # 再交给 gate2(out["p"]) / 执行层
rows = load_ledger("ledger.jsonl")
print(hctdip.scorecard(rows, known_code_hashes={rows[0]["code_hash"]: "my runner v1"}))
```

调用抛异常 → 无决策值 + 无因果记录 → 必然 VETO，不会放行。

**实测**（2026-09-29）：100 条真实 Jev 调用全部 ACT（延迟最高 0.74s，tokens 最高 400）；
100 条物理故障注入（A1/A4/A6/BOUNDARY 各 25）拦截 100%，正常对照组 0 误拦。
两组数据都在 `tests/data/`，测试逐条复现闸门结论。

### 因果记录 + 审计前置验证（0.2.0）

每次 `gate()` 返回 `gate_evidence`，4 条规则各一条，ACT 也记：

```json
{"rule": "A1_DELAY", "check": "latency <= max_latency (s)", "value": 0.33, "limit": 1.5, "passed": true, "ts": "2026-09-29T15:14:35.356Z"}
```

出成绩单前必须过两条验证，任何一条不通过 → `AuditRejected`（拒审）：

```python
from jevaudit import require_verified, AuditRejected
KNOWN = {"src-0daa5ad4e46b": "runner v1", "src-...": "runner v2"}   # 已知版本指纹登记表（历史版本也登记）
try:
    require_verified(rows, KNOWN)   # = verify_ledger_integrity + verify_causal_completeness
except AuditRejected as e:
    print(e.report)                 # 逐条失败明细
```

- `verify_ledger_integrity()`：用账本里存的 `input_payload` / `raw_response` 重算 input_hash / output_hash；code_hash 必须在登记表里（不给登记表 = 全部失败，fail-closed）
- `verify_causal_completeness()`：4 条证据齐全、字段齐全、passed 与 value/limit 自洽、ACT/VETO 与证据一致；`audit_mode` 必须是 LIVE / REPLAY，REPLAY 必须同时有 `call_ts` 和 `replay_ts`

### physical_gate（并入自 0.1.1）

```python
from jevaudit import require_task_env, log_action
task, agent = require_task_env()          # 缺 R0T_TASK_ID / R0T_AGENT_ID → exit 2
log_action(task, agent, "hctdip:VETO", evidence=gate_result)   # 目录：参数 > R0T_LOG_DIR > /var/minis/shared/logs/r0t
```

`audited_call(..., task_id=task, agent_id=agent)` 会自动把每次闸门结果连同证据写进 R0-T 日志。

## Gotchas（三轮实战沉淀，踩不到的坑）

1. **Brier 难看时，第一步永远是证伪校验基准**（真值语义/价格语义/结算语义），
   第二步才怀疑模型——两次实战：真值生成器语义反了 → Brier 必然反向
2. **price 列严禁精确校验**：展示价是截断/舍入价（残差恒 < 0.0125），
   真执行价 = `implied_price(usdcSize/size)`；校验用 tick 级容差（0.01/0.001）
3. **check_spec 必填**：记录用的什么基准/容差，让"度量分歧"可回溯，
   不然下个人又把基准当真值查一遍
4. **["0","0"] 是退化态**：已结算二元市场必须恰好一边=1，两边全 0 = 未真结算，拒
5. **outcomePrices 可能是字符串**（JSON 编码数组），先 json.loads 再算
6. **残差 <0 的反例（~1.4%）疑似 SELL 侧语义**：标注 `reverse`，不判死不混入
7. **样本 <3 不下结论**；漂移率 |mean_p − mean_o| > 0.01 如实打 ❌，不圆场

## 配套

- **jevkit**（PyPI）：OpenRouter Decisions API (Jev) 的第一个开源第三方客户端
  —— client + CLI + gate 模式
- 实战战绩：n=100 黑箱审计（Polymarket 真实交易 vs Jev，前视防护 100/100）

## License

MIT

## 独立对账 `jevaudit.audit`（未发布到 PyPI，源码安装）

闸门只能审到了它面前的记录，审不到从没写进账本的记录。`jevaudit.audit` 在事后拿独立来源重读一遍再对账：

- `verify_ledger_integrity(records, known_code_hashes)`：逐条核对 output_hash，code_hash 必须登记，查重复。
- `reconcile_with_chain(records, rpcs, lo, hi, wallets)`：区块 lo..hi 从链上重拉，和账本逐笔比；
  有差异的区块再问 3 个独立节点，按多数结果定；input_hash 用原始链上 log 重算。节点没答复记为「无答复」，不当成「没有数据」。

```bash
pip install "jevaudit @ git+https://github.com/HCTDIP/jevaudit"
python -m jevaudit.audit reconcile --ledger-glob 'ledgers/*.jsonl.gz' --wallets wallets.json \
  --known-code-hash <sha256> --out-dir reconcile
```

真实案例见 [CASES.md](CASES.md)：监听器自报完整，独立对账发现 61 笔缺口。
