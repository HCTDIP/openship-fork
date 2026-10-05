# Cases

## 2026-10-03 — 监听器自报完整，独立对账发现 61 笔缺口

### 1. 监听器的自报状态

- 对象：Polymarket 链上监听器 v2（只记录，不交易），监听 Polygon 上 3 个交易所合约的 `OrderFilled`，
  只记 2,182 个被跟踪钱包作为 maker 的成交。代码 sha256 `68e29fd1…`，全程只有这一个版本。
- 运行：2026-10-02 10:46 → 2026-10-03 12:15 UTC，账本 781,199 行。
- 结束时的状态文件 `chain_listener_status.json`：`lag_blocks: 0`，`fills_total: 781140`。
- 事件日志：24 小时内只有 1 次错误（10-03 10:53 UTC 的 RPC 超时），和下面 5 个区块无关。
- 闸门（HCTDIP gate：A1 延迟 / A4 因果链 / A6 算力 / BOUNDARY）在这段时间没有报警。

从监听器自己的记录看，这次运行没有异常。

### 2. 独立对账的方法

- 不用监听器的代码和状态，从链上把区块 94821879–94883060（61,182 个区块）的全部 `OrderFilled`
  重新拉了一遍：3,900,170 笔成交。
- 按同一份钱包名单过滤后，和账本逐笔对比（键 = 交易哈希 + logIndex）。
- 对有差异的区块，用 3 个互不相关的公共节点（drpc.org、1rpc.io、publicnode.com）分别再查，三个节点结果一致。
- 账本里每笔记录的 `input_hash`（原始链上 log 的 sha256）可以用重拉的原始 log 重新算出来，三个节点都对得上。

### 3. 发现

账本里的 781,065 笔（去重后）在链上全部能找到，没有编造的记录。但有 5 个区块在链上有被跟踪钱包的成交，账本里一笔都没有：

| 区块 | 时间 (UTC) | 链上成交（全部） | 链上成交（被跟踪钱包） | 账本 | 前一个区块在账本里重复的行数 |
|---|---|---|---|---|---|
| 94834428 | 10-02 15:59 | 83 | 16 | 0 | 22 |
| 94840332 | 10-02 18:27 | 38 | 12 | 0 | 35 |
| 94862944 | 10-03 03:52 | 32 | 1 | 0 | 12 |
| 94875380 | 10-03 09:03 | 138 | 29 | 0 | 49 |
| 94875386 | 10-03 09:03 | 40 | 3 | 0 | 16 |
| **合计** | | **331** | **61** | **0** | **134** |

- 缺 61 笔，占链上被跟踪成交 781,126 笔的 0.008%。
- 账本里的 134 行重复记录全部落在这 5 个区块各自的前一个区块里。

逐笔清单：[`cases/2026-10-03-tip-race/missing_fills.csv`](cases/2026-10-03-tip-race/missing_fills.csv)
（61 行：区块、交易哈希、logIndex、maker；sha256 `7e3f2074…`）。这些都是公开链上数据，任何 Polygon 节点都可以复查。

### 4. 根因（tip race）

5 次的情况完全一样：

1. 一轮读到区块 m-1，当时节点报告的最新区块 = m-1。
2. 下一轮节点报告最新区块 = m，监听器请求 `eth_getLogs [m, m]`。
3. 节点没有报错，但返回的是 **m-1 的 log**。这个节点当时还没准备好 m 的数据。
4. 监听器把这些 log 又写了一遍（m-1 出现重复），然后把游标推进到 m+1。m 再也没被读过。

账本里能直接看到这个过程：m-1 的第二份记录上写的 `head = m`，`detect_ts` 比第一份晚约 1 秒。

更正：此前的 24 小时审计报告把这 134 行重复写成「断点续跑时的重叠区块」，这个说法不对。它们和 5 个漏块是同一个原因。原报告文件保持原样，没有改动。

### 5. 启示

- 闸门审查的是到了它面前的每一条记录：够不够新、有没有因果链、有没有超算力、有没有越界。
  一条根本没被写进账本的记录不会到闸门面前，闸门也就没法拦它。
- 监听器自己的状态文件说「落后 0 个区块」，在它的视角里这是真的：游标确实已经走到了链头。
- 发现缺口的唯一办法，是拿一个独立来源重新读一遍，再和账本对账。

### 6. 之后的改动

- 监听器 v3：只读 `head - CONFIRM_DEPTH`（默认 10）以内的区块；返回了请求范围以外区块的答复，整次作废；
  每个区块明确记为 `DATA_PRESENT` / `CONFIRMED_EMPTY`（两个不同节点都说没有）/ `UNAVAILABLE`（游标停在它前面）。
  附带复现这次事故的回归测试。
- 对账独立成 `jevaudit.audit`：`verify_ledger_integrity()`（三指纹）和 `reconcile_with_chain()`（3 个节点逐笔对比）。
  用这次的账本测试，5 个区块、61 笔、134 行重复全部能查出来。

```bash
python -m jevaudit.audit reconcile --ledger-glob 'ledgers/chain_listener_*.jsonl.gz' \
  --wallets wallets.json --known-code-hash <listener sha256> --out-dir reconcile
```

### 证据（[HCTDIP/openship-fork](https://github.com/HCTDIP/openship-fork)，原始文件，未加工）

| 文件 | sha256 |
|---|---|
| [`raw/ledger/chain_listener_20261002.jsonl.gz`](https://github.com/HCTDIP/openship-fork/blob/main/raw/ledger/chain_listener_20261002.jsonl.gz) | `5d57a98d53052c4f9490f6478ebaa2b3917282207dfae21fdca0fe01bf3a7e22` |
| [`raw/ledger/chain_listener_20261003.jsonl.gz`](https://github.com/HCTDIP/openship-fork/blob/main/raw/ledger/chain_listener_20261003.jsonl.gz) | `16fce6de4b5ec6217aa94da677c00b01462eed506eda63bfb1773401603b7be5` |
| [`raw/ledger/chain_listener_status.json`](https://github.com/HCTDIP/openship-fork/blob/main/raw/ledger/chain_listener_status.json) | `655737071dfdc559169157dc8985216744cd6c504e0f0c7075303ff63b42d854` |
| [`raw/ledger/chain_listener_events.jsonl`](https://github.com/HCTDIP/openship-fork/blob/main/raw/ledger/chain_listener_events.jsonl) | `5f0a56dd7904f471a0d2c8a311d25139d9e0c332f7badfccb28cc25edd71d576` |
| [`raw/repull/gl/94834179.parquet`](https://github.com/HCTDIP/openship-fork/blob/main/raw/repull/gl/94834179.parquet)（含 94834428） | `18aee44d15a7c256bf7eb5c2c4a974e25535bf12ca7b9d1a5d08b3e2e0896b1c` |
| [`raw/repull/gl/94840179.parquet`](https://github.com/HCTDIP/openship-fork/blob/main/raw/repull/gl/94840179.parquet)（含 94840332） | `db1d3fdb9572c429877f1471c5cc618b2b8a847f6739971ccb2f47957eb27c33` |
| [`raw/repull/gl/94862679.parquet`](https://github.com/HCTDIP/openship-fork/blob/main/raw/repull/gl/94862679.parquet)（含 94862944） | `1c378bcd5b0ec8aa0d26bfb40487628ee4749a41cf05cda155ab9014dbc6b398` |
| [`raw/repull/gl/94875279.parquet`](https://github.com/HCTDIP/openship-fork/blob/main/raw/repull/gl/94875279.parquet)（含 94875380、94875386） | `d45ad9818f2111a3008f8f737c8a8f5f88fcde66f2cc17a244dfe57cf4c529d6` |
| [`listener/listener.py`](https://github.com/HCTDIP/openship-fork/blob/main/listener/listener.py)（v3） | `0bd0618033e0ba07147406c3a4a63fff89999ca8bf47544aaeeb0bd88bd2eb99` |

全部文件的 sha256 清单：[`SHA256SUMS.txt`](https://github.com/HCTDIP/openship-fork/blob/main/SHA256SUMS.txt)。克隆后在仓库根目录 `sha256sum -c SHA256SUMS.txt` 即可复核（fills.parquet 以 .gz 存放，见 MANIFEST_NOTES.txt）；10-05 新增文件见 `SHA256SUMS_20261005.txt`。
