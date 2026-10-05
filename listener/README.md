# chain-listener v3 (只读，不下单，不调 Polymarket API)

- 只监听 Polygon 上 3 个 Polymarket 交易所合约的 OrderFilled，maker 属于 wallets.json（2,182 个）才记。
- 账本：$LEDGER_DIR/chain_listener_<YYYYMMDD>.jsonl.gz（UTC 日期，gzip，每轮 flush+fsync）。
- 每条记录三指纹：input_hash（原始 log 的 sha256）、output_hash（本条记录的 sha256）、code_hash（listener.py 的 sha256）。
- 延迟 latency_s = 本机收到时间 - 区块时间。drpc 的 getLogs 自带 blockTimestamp，平时不用额外调用；缺了才调 eth_getBlockByNumber。
- 断点续跑：chain_listener_state.json 记下一个区块；72h 从第一次启动算。磁盘剩余 <30MB 自动停。
- 只用 Python 标准库，内存几十 MB。

运行：`python listener.py`（环境变量 LEDGER_DIR / DURATION_H / TRACK=all|wallets / RPCS）
报告：`python report.py <LEDGER_DIR> --enrich`（--enrich 才离线查 Gamma 补 market，不在监听时调用）

## v3 (2026-10-05)
- 只读 head-CONFIRM_DEPTH（默认 10）以内的区块；每个区块三种状态 DATA_PRESENT / CONFIRMED_EMPTY / UNAVAILABLE，见 CHANGELOG.md。
- 测试：`python -m unittest test_listener -v`
