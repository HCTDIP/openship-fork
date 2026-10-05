"""T4: tip race reproduced against listener v3 (openship-fork listener/listener.py) with a mock RPC.
Prints every mock RPC call and answer. No network, no production files."""
import gzip, json, os, sys, tempfile

TMP = tempfile.mkdtemp(prefix="t4_")
os.environ.update(LEDGER_DIR=TMP, TRACK="all", RPCS="http://node-a,http://node-b,http://node-c")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "listener"))
import listener as L  # noqa: E402

EX = sorted(L.EXCHANGES)[0]
OUT = []


def say(s):
    OUT.append(s)
    print(s)


def mk_log(bn, i=0):
    w = lambda n: "%064x" % n
    return {"address": EX, "blockNumber": hex(bn), "logIndex": hex(i), "transactionHash": "0x" + w(bn * 100 + i),
            "blockTimestamp": hex(1_790_000_000 + bn * 2), "topics": [L.TOPIC, "0x" + w(1), "0x" + w(0xAAAA), "0x" + w(0xBBBB)],
            "data": "0x" + w(0) + w(123) + w(1_000_000) + w(2_000_000) + w(0)}


class Node:
    def __init__(self, name, head, has_upto, down=False):
        self.name, self.head, self.has_upto, self.down = name, head, has_upto, down

    def call(self, m, p):
        if self.down:
            say(f"    rpc {self.name} {m} -> NO ANSWER (timeout)")
            raise TimeoutError("down")
        if m == "eth_blockNumber":
            say(f"    rpc {self.name} eth_blockNumber -> {self.head}")
            return hex(self.head)
        a, b = int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16)
        logs = [mk_log(bn) for bn in range(a, b + 1) if bn <= self.has_upto]
        got = sorted({int(l["blockNumber"], 16) for l in logs})
        say(f"    rpc {self.name} eth_getLogs({a},{b}) -> {len(logs)} logs, blocks {got[:1] + (['..', got[-1]] if len(got) > 1 else [])}")
        return logs


def use(nodes):
    t = dict(zip(L.RPCS, nodes))
    L._post = lambda url, m, p: t[url].call(m, p)


def ledger_blocks():
    L.close_ledger()
    out = []
    for f in os.listdir(TMP):
        if f.startswith("chain_listener_2"):
            with gzip.open(os.path.join(TMP, f), "rt") as g:
                out += [json.loads(x)["block"] for x in g]
    return out


def reset(depth, lp):
    L.close_ledger()
    for f in os.listdir(TMP):
        os.remove(os.path.join(TMP, f))
    L.CONFIRM_DEPTH = depth
    return L.migrate_state({"start_ts": 0, "next_block": lp + 1, "fills": 0})


def show(st, res):
    say(f"  -> states {dict(sorted(res.get('states', {}).items())[-3:])} skipped={res.get('skipped')}")
    say(f"  -> last_processed={st['last_processed']} pending_tip={st['pending_tip']} last_confirmed={st['last_confirmed']} "
        f"block_states={st['block_states']}")


def check(name, cond):
    say(f"  ASSERT {name}: {'OK' if cond else 'FAILED'}")
    return cond


def run(title, depth, lp, r1_nodes, r2_nodes, want1, want2):
    say(f"\n=== {title} (CONFIRM_DEPTH={depth}) ===")
    st = reset(depth, lp)
    say(f"round 1: head=256, node data up to 255")
    use(r1_nodes)
    res = L.poll_once(st, None)
    show(st, res)
    ok = all([check(f"last_processed == {want1}", st["last_processed"] == want1),
              check("pending_tip == 256", st["pending_tip"] == 256),
              check("256 not marked DATA_PRESENT/CONFIRMED_EMPTY", res["states"].get(256) in (None, L.BlockState.UNAVAILABLE)),
              check("256 not in ledger", 256 not in ledger_blocks())])
    say(f"round 2: head={r2_nodes[0].head}, block 256 now has data")
    use(r2_nodes)
    res = L.poll_once(st, None)
    show(st, res)
    b = ledger_blocks()
    ok &= all([check(f"last_processed == {want2}", st["last_processed"] == want2),
               check("256 written exactly once", b.count(256) == 1),
               check("no block written twice", len(b) == len(set(b)))])
    say(f"RESULT {title}: {'PASS' if ok else 'FAIL'}")
    return ok


N = lambda name, head, upto, down=False: Node(name, head, upto, down)
r = {}
r["T4a"] = run("T4a production config", 10, 240,
               [N("a", 256, 255), N("b", 256, 255), N("c", 256, 255)],
               [N("a", 266, 266), N("b", 266, 266), N("c", 266, 266)], 246, 256)
r["T4b"] = run("T4b depth off, only node a reachable (literal spec)", 0, 254,
               [N("a", 256, 255), N("b", 256, 255, True), N("c", 256, 255, True)],
               [N("a", 256, 256), N("b", 256, 256, True), N("c", 256, 256, True)], 255, 256)
r["T4c"] = run("T4c depth off, all 3 nodes lag at the tip (edge)", 0, 254,
               [N("a", 256, 255), N("b", 256, 255), N("c", 256, 255)],
               [N("a", 256, 256), N("b", 256, 256), N("c", 256, 256)], 255, 256)
say("\n" + json.dumps(r))
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "reconcile", "T4_mock_rpc_output.txt"), "w").write("\n".join(OUT) + "\n")
json.dump(r, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "reconcile", "T4.json"), "w"))
