"""jevaudit 包验证测试：从包内 import（不是从 mm-audit-poc 平铺目录）。
跑通 = 包结构 + 模块 + 测试全绿，发 PyPI 前的验收。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jevaudit import (canonical, input_hash, output_hash, code_hash, add_record,
                      load_ledger, gate2, GateResult, brier_score, calibration_curve,
                      implied_price, residual_check, match_tick,
                      require_task_env, log_action)


class TestPackage(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(canonical({"b": 1, "a": "x"}), '{"a":"x","b":1}')

    def test_hashes(self):
        self.assertEqual(len(input_hash("s", {"a": 1})), 32)
        self.assertTrue(code_hash().startswith("src-"))

    def test_gate_bands(self):
        self.assertIs(gate2(0.70), GateResult.KEEP)
        self.assertIs(gate2(0.5), GateResult.CONFIRM)
        self.assertIs(gate2(0.2), GateResult.DROP)

    def test_brier_and_baseline(self):
        # 全猜 0.5 的 Brier = 0.25 常数（与分布无关）
        self.assertAlmostEqual(brier_score([0.5, 0.5], [1, 0]), 0.25)
        self.assertAlmostEqual(brier_score([0.9, 0.2], [1, 0]), 0.025)

    def test_ledger_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.jsonl")
            add_record(p, {"id": "x", "p": 0.8, "outcome": 1,
                           "input_hash": "a" * 32, "output_hash": "b" * 32,
                           "code_hash": "src-c" * 2, "check_spec": {"tick": 0.01}})
            rows = load_ledger(p)
            self.assertEqual(rows[0]["check_spec"]["tick"], 0.01)

    def test_price_sem(self):
        self.assertAlmostEqual(implied_price(100, 56.24), 0.5624)
        self.assertEqual(residual_check(0.55, 0.5624), "ok")
        self.assertEqual(residual_check(0.56, 0.55), "reverse")

    def test_code_hash_caller_default(self):
        # code_hash 默认取调用方脚本（不是不存在的 mm_poc.py）
        h = code_hash()  # 本测试脚本就是调用方
        self.assertTrue(h.startswith("src-") and len(h) == 16)

    def test_physical_gate_fail_closed(self):
        # 物理闸：缺任务 ID/代理身份 → SystemExit(2)（fail-closed）
        old = {k: os.environ.get(k) for k in ("R0T_TASK_ID", "R0T_AGENT_ID")}
        for k in old:
            os.environ.pop(k, None)
        with self.assertRaises(SystemExit) as cm:
            require_task_env()
        self.assertEqual(cm.exception.code, 2)
        for k, v in old.items():  # 恢复现场
            if v is not None:
                os.environ[k] = v


if __name__ == "__main__":
    unittest.main(verbosity=2)
