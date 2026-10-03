#!/usr/bin/env python3
"""test_jevkit.py — jevkit 测试（需要 OPENROUTER_API_KEY 才跑真调用）。

用法:
    python3 -m jevkit.test_jevkit          # 真调用测试
"""
import os
import unittest


class TestGate(unittest.TestCase):
    """gate 决策门（不需要 key）。"""

    def test_act(self):
        from jevkit import gate
        self.assertEqual(gate(0.8), 'act')
        self.assertEqual(gate(0.7), 'act')

    def test_confirm(self):
        from jevkit import gate
        self.assertEqual(gate(0.6), 'confirm')
        self.assertEqual(gate(0.5), 'confirm')

    def test_escalate(self):
        from jevkit import gate
        self.assertEqual(gate(0.4), 'escalate')
        self.assertEqual(gate(0.0), 'escalate')

    def test_borderline_flips(self):
        """borderline 会翻转 — confirm 档不自动执行（坑 2）。"""
        from jevkit import gate
        # 0.5-0.7 区间永远 confirm，不会 act
        for p in (0.5, 0.55, 0.6, 0.65, 0.69):
            self.assertEqual(gate(p), 'confirm', f'{p} 应该是 confirm（不自动执行）')


class TestClient(unittest.TestCase):
    """Client（需要 key — 真调用）。"""

    def setUp(self):
        if not os.environ.get('OPENROUTER_API_KEY'):
            self.skipTest('OPENROUTER_API_KEY not set')

    def test_noul_real(self):
        from jevkit import Client
        client = Client()
        p = client.noul(
            'worth_outreach',
            'Is this lead worth cold outreach?',
            {'true': 'Explicit budget + concrete need.',
             'false': 'No budget or self-promotion.'},
            state='HN post: [Hiring] N8n automation expert, $2k budget, urgent',
        )
        self.assertIsInstance(p, float)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_no_key_error(self):
        """无 key 时 RuntimeError（fallback 友好 — except Exception 能接住）。"""
        from jevkit import Client
        saved = os.environ.pop('OPENROUTER_API_KEY', None)
        try:
            client = Client(api_key='')
            with self.assertRaises(RuntimeError):
                client.noul('q', 'i', {'true': 't', 'false': 'f'}, state='s')
        finally:
            if saved:
                os.environ['OPENROUTER_API_KEY'] = saved


if __name__ == '__main__':
    unittest.main(verbosity=2)
