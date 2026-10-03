# HCTDIP 闸门 + 审计成绩单

    pip install pytest && pytest -q test_gate.py      # 7 条测试，gate() 验收
    export OPENROUTER_API_KEY=sk-or-...
    python run_audit.py run        # 100 条真实 Jev 调用 → gate() → ledger.jsonl（outcome 空）
    python run_audit.py backfill   # 回填真实结果 1/0（Polymarket 已结算市场）
    python run_audit.py report     # scorecard.json + report.md

只用标准库，内存 <50MB，iSH 也能跑；约 2 分钟，成本约 $0.002。
样本：markets_pool.json 1815 个已结算市场，seed=42，按 YES/NO 各 50 条分层抽样，保证有坏决策样本。
决策分类：p≥0.7 押 YES，p≤0.3 押 NO，方向对 = 好，方向错 = 坏，0.3<p<0.7 = 模糊。
参数是占位值，在 gate.py 的 DEFAULTS 里改。
