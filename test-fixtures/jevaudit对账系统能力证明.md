# jevaudit 对账系统能力证明（v0.3.0-freeze）

日期：2026-10-05 · 对象：openship-fork 24h 账本副本（raw/ledger/ 字节复制到 test-fixtures/ledger_original/，生产账本未改）

**结论：9/9 通过**（T1、T2、T3、T3.5、T3.6、T5、T4、T4c、61/61）。

- listener.py sha256 `590c1bdd13149f1b7ab6ea05ec99883594d588f7e4f327a9a83ae1a54ef4f4e4`（tag `listener-v0.3.0-freeze`）
- test_listener.py sha256 `fee300712bf7329cab56d94e5e4a13f4f74608372df1da41b87239c3b66f8986`，13 个单元测试全部 OK
- 第一次跑（修复前，listener v3 `0bd06180`）：T4c 失败，其余通过。之前消息里写的「T4c PASS」不对，当时实际是 FAIL。修复后本次全部重跑，T4c 通过。修复后输出在第 3 节，修复前输出在第 4 节。

## 1. 方法

- 每项测试单独 fixture_id：备份 → 注入 → 对账 → 恢复 → SHA256 对比（restore_and_verify.sh 自动执行）。恢复后 SHA256 不一致 → 整轮作废。
- 对账 = 从 Polygon RPC 重拉 OrderFilled 日志，按 2182 个跟踪钱包过滤，与账本逐笔比对（tx + log_index）；每窗口 10 个区块用 3 个节点交叉核对。
- 判定：只出现 1 条、且与预期一致的 finding 才算通过；多报或少报都算失败。
- T1 的伪造记录重新计算了 output_hash，所以账本自身的指纹检查发现不了，只能靠链上比对发现。
- 中间窗口 W = 94860000–94860039（事先确认无已知缺口）；尾部窗口 94883008–94883047（最后区块 94883047 取自 checkpoint next_block−1）。
- gzip 文件被改后重新压缩，字节必然不同，所以被改文件比对解压后内容的 SHA256；未改文件比对文件字节 SHA256。每项测试都核对生产账本未变。
- 61/61 只在前面全部通过后才执行（restore_and_verify.sh 里写死）。
- 范围说明：T1–T3.6、T5、61/61 测的是对账（jevaudit/audit），T4/T4c 测的是 listener.py。listener 修复不影响对账代码，但按指令全部重跑。

## 2. 逐项记录

| fixture_id | 注入 | 预期 | 实际 | 原始 sha256（内容） | 注入后 sha256（内容） | 恢复后一致 | 结果 |
|---|---|---|---|---|---|---|---|
| T1 | 1 fake fill in block 94860000, tx 0xe9948b45335d3e4d619c1ac327b592b2f116155d5bfadb234a79ad66fbf6ac52, log_index 1063 (output_hash recomputed) | exactly 1 x EXTRA_IN_LEDGER block=94860000 tx=0xe9948b45335d3e4d619c1ac327b592b2f116155d5bfadb234a79ad66fbf6ac52 | EXTRA_IN_LEDGER 94860000 | `28e5a7f504255aeb` | `c50d44eb3a7723b1` | 是 | 通过 |
| T2 | delete 1 real fill: block 94860001, tx 0x773c784c221fc71236b18dcbb25faea026d704fc45f06e13be6dad0c3a0050c8, log_index 224 | exactly 1 x MISSING_FROM_LEDGER block=94860001 tx=0x773c784c221fc71236b18dcbb25faea026d704fc45f06e13be6dad0c3a0050c8 | MISSING_FROM_LEDGER 94860001 | `28e5a7f504255aeb` | `c13e617b170eadbb` | 是 | 通过 |
| T3 | delete all 23 fills of block 94860002 | exactly 1 x MISSING_BLOCK block=94860002 missing_fills=23 | MISSING_BLOCK 94860002 (23笔) | `28e5a7f504255aeb` | `b0aa4eb9dd782668` | 是 | 通过 |
| T3.5 | delete last 5 blocks 94883043..94883047 (51 fills) | exactly 1 x TRAILING_GAP blocks=[94883043, 94883044, 94883045, 94883046, 94883047] missing_fills=51 | TRAILING_GAP (51笔) | `28e5a7f504255aeb` | `58390d6259bafe44` | 是 | 通过 |
| T3.6 | delete file chain_listener_20261003.jsonl.gz | exactly 1 x MISSING_DAY_PARTITION day=20261003 | MISSING_DAY_PARTITION | `28e5a7f504255aeb` | `(file deleted)` | 是 | 通过 |
| T5 | nothing | PASS (no finding) on middle window, tail window, partitions, integrity | PASS; PASS | `28e5a7f504255aeb` | `28e5a7f504255aeb` | 是 | 通过 |
| T4 | mock RPC：head=256，255 有数据，256 空 | UNAVAILABLE / 不推进游标 / pending_tip=256；第二轮写入 256 | T4a、T4b 全部断言 OK（第 3 节） | — | — | — | 通过 |
| T4c | mock RPC：CONFIRM_DEPTH=0，节点在 tip 同时滞后，两个节点都对 256 返回空 | 256 = UNAVAILABLE，游标停在 255，第二轮写入 256 | 全部断言 OK（第 3、4 节） | — | — | — | 通过 |
| 61/61 | 不注入，真实 24h 事故 | 5 区块 61 笔 | 5/5 区块，61/61 笔 | — | — | — | 通过 |

原始文件 sha256（chain_listener_20261003.jsonl.gz 字节）：`16fce6de4b5ec6217aa94da677c00b01462eed506eda63bfb1773401603b7be5`；解压内容：`28e5a7f504255aeb1f354673e3bcfcc1a315c17eb900a4480d64def7bb1863f0`。20261002 分片：`5d57a98d53052c4f9490f6478ebaa2b3917282207dfae21fdca0fe01bf3a7e22`。

### 每项恢复验证明细

- T1 · chain_listener_20261002.jsonl.gz · file · 恢复 `5d57a98d53052c4f` = 原始 `5d57a98d53052c4f` → 一致
- T1 · chain_listener_20261003.jsonl.gz · content · 恢复 `28e5a7f504255aeb` = 原始 `28e5a7f504255aeb` → 一致
- T1 · 生产账本未变：是
- T2 · chain_listener_20261002.jsonl.gz · file · 恢复 `5d57a98d53052c4f` = 原始 `5d57a98d53052c4f` → 一致
- T2 · chain_listener_20261003.jsonl.gz · content · 恢复 `28e5a7f504255aeb` = 原始 `28e5a7f504255aeb` → 一致
- T2 · 生产账本未变：是
- T3 · chain_listener_20261002.jsonl.gz · file · 恢复 `5d57a98d53052c4f` = 原始 `5d57a98d53052c4f` → 一致
- T3 · chain_listener_20261003.jsonl.gz · content · 恢复 `28e5a7f504255aeb` = 原始 `28e5a7f504255aeb` → 一致
- T3 · 生产账本未变：是
- T3.5 · chain_listener_20261002.jsonl.gz · file · 恢复 `5d57a98d53052c4f` = 原始 `5d57a98d53052c4f` → 一致
- T3.5 · chain_listener_20261003.jsonl.gz · content · 恢复 `28e5a7f504255aeb` = 原始 `28e5a7f504255aeb` → 一致
- T3.5 · 生产账本未变：是
- T3.6 · chain_listener_20261002.jsonl.gz · file · 恢复 `5d57a98d53052c4f` = 原始 `5d57a98d53052c4f` → 一致
- T3.6 · chain_listener_20261003.jsonl.gz · file · 恢复 `16fce6de4b5ec621` = 原始 `16fce6de4b5ec621` → 一致
- T3.6 · 生产账本未变：是
- T5 · chain_listener_20261002.jsonl.gz · file · 恢复 `5d57a98d53052c4f` = 原始 `5d57a98d53052c4f` → 一致
- T5 · chain_listener_20261003.jsonl.gz · file · 恢复 `16fce6de4b5ec621` = 原始 `16fce6de4b5ec621` → 一致
- T5 · 生产账本未变：是

### T5 假阳性明细

- middle [94860000, 94860039]：账本 464 笔，链上跟踪钱包 464 笔，对上 464，缺 0，多 0；指纹失败 0；三节点抽查 10/10 一致
- tail [94883008, 94883047]：账本 418 笔，链上跟踪钱包 418 笔，对上 418，缺 0，多 0；指纹失败 0；三节点抽查 10/10 一致
- 分片：应有 ['20261002', '20261003']，实有 ['20261002', '20261003']，缺 []
- T5 只在两个 40 区块窗口上跑（省积分），不是全 24h 重拉。全量 3,900,170 笔重拉是之前做的那次。

## 3. T4 / T4c mock RPC 输出全文（本次，修复后）

```
=== T4a production config (CONFIRM_DEPTH=10) ===
round 1: head=256, node data up to 255
    rpc a eth_blockNumber -> 256
    rpc a eth_getLogs(241,246) -> 6 logs, blocks [241, '..', 246]
  -> states {244: 'DATA_PRESENT', 245: 'DATA_PRESENT', 246: 'DATA_PRESENT'} skipped=False
  -> last_processed=246 pending_tip=256 last_confirmed=246 block_states={'DATA_PRESENT': 6, 'CONFIRMED_EMPTY': 0, 'UNAVAILABLE': 0}
  ASSERT last_processed == 246: OK
  ASSERT pending_tip == 256: OK
  ASSERT 256 not marked DATA_PRESENT/CONFIRMED_EMPTY: OK
  ASSERT 256 not in ledger: OK
round 2: head=266, block 256 now has data
    rpc a eth_blockNumber -> 266
    rpc a eth_getLogs(247,256) -> 10 logs, blocks [247, '..', 256]
  -> states {254: 'DATA_PRESENT', 255: 'DATA_PRESENT', 256: 'DATA_PRESENT'} skipped=False
  -> last_processed=256 pending_tip=266 last_confirmed=256 block_states={'DATA_PRESENT': 16, 'CONFIRMED_EMPTY': 0, 'UNAVAILABLE': 0}
  ASSERT last_processed == 256: OK
  ASSERT 256 written exactly once: OK
  ASSERT no block written twice: OK
RESULT T4a production config: PASS

=== T4b depth off, only node a reachable (literal spec) (CONFIRM_DEPTH=0) ===
round 1: head=256, node data up to 255
    rpc a eth_blockNumber -> 256
    rpc a eth_getLogs(255,256) -> 1 logs, blocks [255]
    rpc b eth_getLogs -> NO ANSWER (timeout)
    rpc c eth_getLogs -> NO ANSWER (timeout)
  -> states {255: 'DATA_PRESENT', 256: 'UNAVAILABLE'} skipped=False
  -> last_processed=255 pending_tip=256 last_confirmed=256 block_states={'DATA_PRESENT': 1, 'CONFIRMED_EMPTY': 0, 'UNAVAILABLE': 1}
  ASSERT last_processed == 255: OK
  ASSERT pending_tip == 256: OK
  ASSERT 256 not marked DATA_PRESENT/CONFIRMED_EMPTY: OK
  ASSERT 256 not in ledger: OK
round 2: head=256, block 256 now has data
    rpc a eth_blockNumber -> 256
    rpc a eth_getLogs(256,256) -> 1 logs, blocks [256]
  -> states {256: 'DATA_PRESENT'} skipped=False
  -> last_processed=256 pending_tip=256 last_confirmed=256 block_states={'DATA_PRESENT': 2, 'CONFIRMED_EMPTY': 0, 'UNAVAILABLE': 1}
  ASSERT last_processed == 256: OK
  ASSERT 256 written exactly once: OK
  ASSERT no block written twice: OK
RESULT T4b depth off, only node a reachable (literal spec): PASS

=== T4c depth off, all 3 nodes lag at the tip (edge) (CONFIRM_DEPTH=0) ===
round 1: head=256, node data up to 255
    rpc a eth_blockNumber -> 256
    rpc a eth_getLogs(255,256) -> 1 logs, blocks [255]
    rpc b eth_getLogs(256,256) -> 0 logs, blocks []
  -> states {255: 'DATA_PRESENT', 256: 'UNAVAILABLE'} skipped=False
  -> last_processed=255 pending_tip=256 last_confirmed=256 block_states={'DATA_PRESENT': 1, 'CONFIRMED_EMPTY': 0, 'UNAVAILABLE': 1}
  ASSERT last_processed == 255: OK
  ASSERT pending_tip == 256: OK
  ASSERT 256 not marked DATA_PRESENT/CONFIRMED_EMPTY: OK
  ASSERT 256 not in ledger: OK
round 2: head=256, block 256 now has data
    rpc a eth_blockNumber -> 256
    rpc a eth_getLogs(256,256) -> 1 logs, blocks [256]
  -> states {256: 'DATA_PRESENT'} skipped=False
  -> last_processed=256 pending_tip=256 last_confirmed=256 block_states={'DATA_PRESENT': 2, 'CONFIRMED_EMPTY': 0, 'UNAVAILABLE': 1}
  ASSERT last_processed == 256: OK
  ASSERT 256 written exactly once: OK
  ASSERT no block written twice: OK
RESULT T4c depth off, all 3 nodes lag at the tip (edge): PASS

{"T4a": true, "T4b": true, "T4c": true}
```

- T4a = 生产配置（CONFIRM_DEPTH=10）：只处理到 head−10=246，256 不碰，pending_tip=256；head 到 266 后 256 正常写入一次。
- T4b = 指令原样场景（深度关掉，只有节点 a 能答）：256 = UNAVAILABLE，last_processed=255，pending_tip=256；第二轮 256 写入、游标到 256。
- T4c = 深度关掉，两个节点对 256 都返回空：256 离 head 不到 MIN_SAFE_DEPTH，判 UNAVAILABLE，游标停在 255；第二轮 256 写入一次。

## 4. T4c 修复前输出全文（第一次跑，listener v3 `0bd06180`）

```
=== T4c depth off, all 3 nodes lag at the tip (edge) (CONFIRM_DEPTH=0) ===
round 1: head=256, node data up to 255
    rpc a eth_blockNumber -> 256
    rpc a eth_getLogs(255,256) -> 1 logs, blocks [255]
    rpc b eth_getLogs(256,256) -> 0 logs, blocks []
  -> states {255: 'DATA_PRESENT', 256: 'CONFIRMED_EMPTY'} skipped=False
  -> last_processed=256 pending_tip=256 last_confirmed=256 block_states={'DATA_PRESENT': 1, 'CONFIRMED_EMPTY': 1, 'UNAVAILABLE': 0}
  ASSERT last_processed == 255: FAILED
  ASSERT pending_tip == 256: OK
  ASSERT 256 not marked DATA_PRESENT/CONFIRMED_EMPTY: FAILED
  ASSERT 256 not in ledger: OK
round 2: head=256, block 256 now has data
    rpc a eth_blockNumber -> 256
  -> states {} skipped=True
  -> last_processed=256 pending_tip=256 last_confirmed=256 block_states={'DATA_PRESENT': 1, 'CONFIRMED_EMPTY': 1, 'UNAVAILABLE': 0}
  ASSERT last_processed == 256: OK
  ASSERT 256 written exactly once: FAILED
  ASSERT no block written twice: OK
RESULT T4c depth off, all 3 nodes lag at the tip (edge): FAIL

{"T4a": true, "T4b": true, "T4c": false}
```

- 修复前：两个节点都返回空 → 256 被判 CONFIRMED_EMPTY，游标越过 256，第二轮因 last_processed=256 直接跳过，256 的数据丢失。和 24h 事故是同一种丢失。
- 修复：只改 listener.py，Gate 未动。见第 5 节。

## 5. 关于配置参数与安全不变量的边界

- 性能参数可调，安全不变量不可调。
- `CONFIRM_DEPTH`（环境变量，默认 10）是性能参数：决定每轮读到 head 往回多少个区块。可以设成 0，启动不会被拒绝。
- `MIN_SAFE_DEPTH = 5` 硬编码在 listener.py，不读任何环境变量（单元测试 `test_floor_is_not_configurable` 验证）。
- 生效深度 = max(CONFIRM_DEPTH, MIN_SAFE_DEPTH)。
- `CONFIRMED_EMPTY` 的条件：至少 2 个不同节点都返回空 **并且** 区块号 < head − 生效深度（严格小于）。不满足就是 `UNAVAILABLE`：游标停在它前面，下一轮重试。
- 所以把 CONFIRM_DEPTH 设成 0 只会让读得更靠近 tip，不会让“节点说空”在 tip 上被当成确认为空。
- 边界值（单元测试验证）：生效深度 5、block 256 时，head=260 → UNAVAILABLE；head=261 → 可判 CONFIRMED_EMPTY。
- 副作用：默认深度 10 时，最新读到的 head−10 区块如果为空，会多等一轮（约 2 秒）才确认为空。不影响数据。

## 6. 61 笔真实案例复现

| 区块 | 对账找到缺失 | 已知缺失 | 前一区块重复记录 | 一致 |
|---|---|---|---|---|
| 94834428 | 16 | 16 | 22 | 是 |
| 94840332 | 12 | 12 | 35 | 是 |
| 94862944 | 1 | 1 | 12 | 是 |
| 94875380 | 29 | 29 | 49 | 是 |
| 94875386 | 3 | 3 | 16 | 是 |
| 合计 | 61 | 61 | 134 | 5/5 |

## 7. 文件

- test-fixtures/：injector.py、t4_tip_race.py、restore_and_verify.sh、mkreport.py、ledger_original.sha256.json；fixtures/<fid>.json（注入明细 + sha）；reconcile/<fid>.json（对账输出）、reconcile/T4_mock_rpc_output.txt、reconcile/case61.json、reconcile/summary.json、reconcile/listener_unittest.txt、run.log。
- 第一次跑的证据原样保留：test-fixtures/reconcile/run1_before_fix/、test-fixtures/fixtures_run1/。
- test-fixtures/ledger_original/ 是 raw/ledger/ 的字节副本（sha 见 ledger_original.sha256.json），不重复推送。
- jevaudit/audit/findings.py（check_partitions、classify）在 HCTDIP/jevaudit。
- listener/CHANGELOG.md v0.3.0-freeze 条目；SHA256SUMS_listener-v0.3.0-freeze.txt。
