"""Regression tests for the 2026-10-03 tip race (5 blocks lost, 61 tracked fills).

Run:  python -m unittest test_listener -v      (stdlib only, no network)

Scenario from the incident: the node reports head=256 but only has logs up to 255.
v2 read 255..256, got logs for 255 only, and moved the cursor to 257 -> block 256 lost forever.
"""
import gzip, json, os, shutil, tempfile, unittest

_TMP = tempfile.mkdtemp(prefix="listener_test_")
os.environ["LEDGER_DIR"] = _TMP
os.environ["TRACK"] = "all"
os.environ["RPCS"] = "http://node-a,http://node-b,http://node-c"
import listener as L  # noqa: E402

EX = sorted(L.EXCHANGES)[0]


def mk_log(bn, i=0):
    w = lambda n: "%064x" % n
    return {
        "address": EX, "blockNumber": hex(bn), "logIndex": hex(i), "transactionHash": "0x" + w(bn * 100 + i),
        "blockTimestamp": hex(1_790_000_000 + bn * 2),
        "topics": [L.TOPIC, "0x" + w(1), "0x" + w(0xAAAA), "0x" + w(0xBBBB)],
        "data": "0x" + w(0) + w(123) + w(1_000_000) + w(2_000_000) + w(0),
    }


class Node:
    """Fake node: `head` it reports, `has_upto` = highest block it actually has logs for.
    down=True -> every call raises (no answer)."""

    def __init__(self, head, has_upto, down=False, empty=()):
        self.head, self.has_upto, self.down, self.empty = head, has_upto, down, set(empty)

    def call(self, method, params):
        if self.down:
            raise TimeoutError("node down")
        if method == "eth_blockNumber":
            return hex(self.head)
        if method == "eth_getLogs":
            a, b = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            return [mk_log(bn) for bn in range(a, b + 1) if bn <= self.has_upto and bn not in self.empty]
        raise AssertionError(method)


class TipRaceTest(unittest.TestCase):
    def setUp(self):
        for f in os.listdir(_TMP):
            os.remove(os.path.join(_TMP, f))
        L.close_ledger()
        self._post, self._depth = L._post, L.CONFIRM_DEPTH

    def tearDown(self):
        L.close_ledger()
        L._post, L.CONFIRM_DEPTH = self._post, self._depth

    def use(self, **nodes):
        urls = dict(zip("abc", L.RPCS))
        table = {urls[k]: v for k, v in nodes.items()}
        L._post = lambda url, m, p: table[url].call(m, p)

    def state(self, last_processed):
        return L.migrate_state({"start_ts": 0, "next_block": last_processed + 1, "fills": 0})

    def ledger_blocks(self):
        L.close_ledger()
        out = set()
        for f in os.listdir(_TMP):
            if f.startswith("chain_listener_2"):
                with gzip.open(os.path.join(_TMP, f), "rt") as g:
                    out |= {json.loads(x)["block"] for x in g}
        return out

    # --- the incident, with the default confirmation depth --------------------------------
    def test_default_depth_never_reads_tip(self):
        L.CONFIRM_DEPTH = 10
        self.use(a=Node(256, 255), b=Node(256, 255), c=Node(256, 255))
        st = self.state(240)
        res = L.poll_once(st, None)
        self.assertEqual(st["last_processed"], 246)          # exactly head - depth, not "anything < 256"
        self.assertEqual(st["pending_tip"], 256)
        self.assertEqual(st["last_confirmed"], 246)
        self.assertNotIn(256, res["states"])                  # 256 not marked at all
        self.assertNotIn(256, self.ledger_blocks())
        self.assertEqual(self.ledger_blocks(), set(range(241, 247)))

    def test_default_depth_skips_round_when_nothing_confirmed(self):
        L.CONFIRM_DEPTH = 10
        self.use(a=Node(256, 255), b=Node(256, 255), c=Node(256, 255))
        st = self.state(246)
        res = L.poll_once(st, None)
        self.assertTrue(res["skipped"])
        self.assertEqual(st["last_processed"], 246)
        self.assertEqual(st["pending_tip"], 256)

    # --- the incident with depth switched off: second-opinion logic alone must hold ----------
    def test_depth0_single_node_empty_is_UNAVAILABLE_not_processed(self):
        L.CONFIRM_DEPTH = 0
        self.use(a=Node(256, 255), b=Node(256, 255, down=True), c=Node(256, 255, down=True))
        st = self.state(254)
        res = L.poll_once(st, None)
        self.assertEqual(st["last_processed"], 255)           # exactly 255: 254 would also be a bug
        self.assertEqual(st["next_block"], 256)
        self.assertEqual(st["pending_tip"], 256)
        self.assertEqual(res["states"][255], L.BlockState.DATA_PRESENT)
        self.assertEqual(res["states"][256], L.BlockState.UNAVAILABLE)
        self.assertEqual(st["block_states"][L.BlockState.UNAVAILABLE], 1)
        self.assertEqual(st["block_states"][L.BlockState.CONFIRMED_EMPTY], 0)
        self.assertEqual(self.ledger_blocks(), {255})

    def test_depth0_second_node_has_data_is_DATA_PRESENT(self):
        L.CONFIRM_DEPTH = 0
        self.use(a=Node(256, 255), b=Node(256, 256), c=Node(256, 256))
        st = self.state(254)
        res = L.poll_once(st, None)
        self.assertEqual(res["states"][256], L.BlockState.DATA_PRESENT)
        self.assertEqual(st["last_processed"], 256)
        self.assertEqual(self.ledger_blocks(), {255, 256})

    def test_two_nodes_empty_is_CONFIRMED_EMPTY(self):
        L.CONFIRM_DEPTH = 10
        self.use(a=Node(300, 300, empty={250}), b=Node(300, 300, empty={250}), c=Node(300, 300))
        st = self.state(245)
        res = L.poll_once(st, None)
        self.assertEqual(res["states"][250], L.BlockState.CONFIRMED_EMPTY)
        self.assertEqual(st["last_processed"], 275)

    def test_unavailable_is_not_an_empty_list(self):
        f = L.Fetch(False, None, "x", "timeout")
        self.assertIsNone(f.logs)
        L._post = lambda url, m, p: (_ for _ in ()).throw(TimeoutError("down"))
        g = L.get_logs_on("http://node-a", 1, 2)
        self.assertFalse(g.ok)
        self.assertIsNone(g.logs)
        L._post = lambda url, m, p: []
        h = L.get_logs_on("http://node-a", 1, 2)
        self.assertTrue(h.ok)
        self.assertEqual(h.logs, [])

    def test_all_nodes_down_does_not_move_cursor(self):
        L.CONFIRM_DEPTH = 10
        nodes = dict(a=Node(256, 255), b=Node(256, 255), c=Node(256, 255))
        self.use(**nodes)
        st = self.state(240)
        for n in nodes.values():
            n.down = True
        with self.assertRaises(RuntimeError):
            L.poll_once(st, None)
        self.assertEqual(st["last_processed"], 240)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
