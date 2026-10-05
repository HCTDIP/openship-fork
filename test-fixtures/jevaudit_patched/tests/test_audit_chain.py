"""Offline tests for jevaudit.audit (mock RPC). Run: python -m pytest tests/test_audit_chain.py  (or unittest)."""
import unittest

from jevaudit.audit import chain as C

EX = sorted(C.EXCHANGES)[0]
W = "0x" + "aa" * 20


def w(n):
    return "%064x" % n


def mk(bn, i, maker=W):
    return {"address": EX, "blockNumber": hex(bn), "logIndex": hex(i), "transactionHash": "0x" + w(bn * 1000 + i),
            "blockTimestamp": hex(1_790_000_000 + bn), "removed": False,
            "topics": [C.TOPIC, "0x" + w(1), "0x" + "0" * 24 + maker[2:], "0x" + w(2)],
            "data": "0x" + w(0) + w(7) + w(1_000_000) + w(2_000_000) + w(0)}


def rec(l, code="c" * 64):
    r = {"block": int(l["blockNumber"], 16), "tx": l["transactionHash"], "log_index": int(l["logIndex"], 16),
         "wallet": W, "input_hash": C.sha(l), "code_hash": code}
    r["output_hash"] = C.sha(r)
    return r


CHAIN = {bn: [mk(bn, 0), mk(bn, 1)] for bn in range(100, 110)}


class Node:
    def __init__(self, chain, down=False, stale=None):
        self.chain, self.down, self.stale = chain, down, stale

    def __call__(self, method, params):
        if self.down:
            raise TimeoutError("down")
        a, b = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
        if self.stale is not None and b > self.stale:
            return [l for bn in range(a, self.stale + 1) for l in self.chain.get(bn, [])] + self.chain.get(self.stale, [])
        return [l for bn in range(a, b + 1) for l in self.chain.get(bn, [])]


class T(unittest.TestCase):
    def setUp(self):
        self._p, self._s = C._post, C.time.sleep
        C.time.sleep = lambda s: None

    def tearDown(self):
        C._post = self._p
        C.time.sleep = self._s

    def nodes(self, *ns):
        urls = [f"http://n{i}" for i in range(len(ns))]
        table = dict(zip(urls, ns))
        C._post = lambda u, m, p, timeout=40: table[u](m, p)
        return urls

    def test_integrity(self):
        rs = [rec(l) for bn in CHAIN for l in CHAIN[bn]]
        self.assertTrue(C.verify_ledger_integrity(rs, {"c" * 64})["ok"])
        self.assertFalse(C.verify_ledger_integrity(rs, set())["ok"])          # fail-closed
        bad = [dict(rs[0], usdc=1.0)] + rs[1:]
        self.assertEqual(C.verify_ledger_integrity(bad, {"c" * 64})["failures"][0]["reasons"], ["output_hash mismatch"])
        dup = rs + [rs[3]]
        self.assertIn("duplicate tx/log_index", C.verify_ledger_integrity(dup, {"c" * 64})["failures"][0]["reasons"])

    def test_finds_missing_block(self):
        rs = [rec(l) for bn in CHAIN if bn != 105 for l in CHAIN[bn]]
        urls = self.nodes(Node(CHAIN), Node(CHAIN), Node(CHAIN))
        r = C.reconcile_with_chain(rs, urls, 100, 109, {W}, step=3, workers=1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["counts"]["missing_in_ledger"], 2)
        self.assertEqual(r["missing_blocks"], [{"block": 105, "missing_fills": 2, "ledger_fills": 0}])
        self.assertEqual(r["counts"]["input_hash_mismatch"], 0)

    def test_clean_ledger_ok(self):
        rs = [rec(l) for bn in CHAIN for l in CHAIN[bn]]
        urls = self.nodes(Node(CHAIN), Node(CHAIN), Node(CHAIN))
        r = C.reconcile_with_chain(rs, urls, 100, 109, {W}, step=4, workers=1)
        self.assertTrue(r["ok"], r)

    def test_fabricated_record_is_extra(self):
        rs = [rec(l) for bn in CHAIN for l in CHAIN[bn]] + [rec(mk(104, 9))]
        urls = self.nodes(Node(CHAIN), Node(CHAIN), Node(CHAIN))
        r = C.reconcile_with_chain(rs, urls, 100, 109, {W}, step=4, workers=1)
        self.assertEqual(r["counts"]["extra_in_ledger"], 1)

    def test_wrong_primary_outvoted(self):
        lying = {bn: ([] if bn == 103 else ls) for bn, ls in CHAIN.items()}
        rs = [rec(l) for bn in CHAIN for l in CHAIN[bn]]
        urls = self.nodes(Node(CHAIN), Node(lying), Node(CHAIN))   # chunk 100 (step 10) starts at node 1
        r = C.reconcile_with_chain(rs, urls, 100, 109, {W}, step=10, workers=1)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["primary_node_wrong"][0]["block"], 103)

    def test_no_answer_is_not_empty(self):
        rs = [rec(l) for bn in CHAIN for l in CHAIN[bn]]
        urls = self.nodes(Node(CHAIN, down=True), Node(CHAIN, down=True), Node(CHAIN, down=True))
        r = C.reconcile_with_chain(rs, urls, 100, 109, {W}, step=5, workers=1)
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["unanswered_chunks"]), 2)
        self.assertEqual(r["counts"]["missing_in_ledger"], 0)

    def test_out_of_range_answer_is_void(self):
        urls = self.nodes(Node(CHAIN, stale=104))
        a = C.get_logs(urls[0], 105, 105, retries=0)
        self.assertFalse(a.ok)
        self.assertIsNone(a.logs)


if __name__ == "__main__":
    unittest.main()
