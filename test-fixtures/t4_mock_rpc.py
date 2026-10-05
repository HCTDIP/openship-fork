"""T4 Tip Race reproduction against listener v3 (listener/listener.py) with a mock RPC. No network.

Round 1: head=256; getLogs(255,256) -> only block 255 has logs, 256 empty (node not ready).
         Second opinion for 256: the other nodes are not synced to 256 yet (JSON-RPC error).
         Expect: 256 = UNAVAILABLE, last_processed=255, pending_tip=256, 256 not in ledger.
Round 2: getLogs(256,256) returns 256's log -> 256 written, last_processed=256.
CONFIRM_DEPTH=0 is used so the listener actually reads the tip (with the default 10 it never
reads 256 at head=256; that is checked as well in the side checks).
"""
import gzip, json, os, sys, tempfile

TMP = tempfile.mkdtemp(prefix="t4_")
os.environ.update(LEDGER_DIR=TMP, TRACK="all", RPCS="http://node-a,http://node-b,http://node-c")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "listener"))
import listener as L  # noqa: E402

EX = sorted(L.EXCHANGES)[0]
CALLS = []
LAST = []


def mk_log(bn, i=0):
    w = lambda n: "%064x" % n
    return {"address": EX, "blockNumber": hex(bn), "logIndex": hex(i), "transactionHash": "0x" + w(bn * 100 + i),
            "blockTimestamp": hex(1_790_000_000 + bn * 2),
            "topics": [L.TOPIC, "0x" + w(1), "0x" + w(0xAAAA), "0x" + w(0xBBBB)],
            "data": "0x" + w(0) + w(123) + w(1_000_000) + w(2_000_000) + w(0)}


class Node:
    def __init__(self, name, head, has_upto, synced_upto):
        self.name, self.head, self.has_upto, self.synced_upto = name, head, has_upto, synced_upto

    def call(self, m, p):
        if m == "eth_blockNumber":
            CALLS.append(f"{self.name} eth_blockNumber -> {self.head}")
            return hex(self.head)
        a, b = int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16)
        if b > self.synced_upto:
            CALLS.append(f"{self.name} eth_getLogs({a},{b}) -> ERROR block range beyond node head {self.synced_upto}")
            raise RuntimeError("rpc error: {'code': -32000, 'message': 'block range extends beyond current head block'}")
        out = [mk_log(bn) for bn in range(a, b + 1) if bn <= self.has_upto]
        CALLS.append(f"{self.name} eth_getLogs({a},{b}) -> blocks {sorted({int(l['blockNumber'],16) for l in out})}")
        return out


def use(nodes):
    t = dict(zip(L.RPCS, nodes))
    L._post = lambda url, m, p: t[url].call(m, p)


def ledger_blocks():
    L.close_ledger()
    s = set()
    for f in os.listdir(TMP):
        if f.startswith("chain_listener_2"):
            with gzip.open(os.path.join(TMP, f), "rt") as g:
                s |= {json.loads(x)["block"] for x in g}
    return sorted(s)


def show(tag, st, res):
    print(f"--- {tag}")
    for c in CALLS:
        print("  rpc:", c)
    LAST[:] = CALLS; CALLS.clear()
    print("  poll result:", json.dumps({k: v for k, v in res.items()}, sort_keys=True, default=str))
    print("  state:", json.dumps({k: st.get(k) for k in ("last_processed", "next_block", "pending_tip", "last_confirmed", "confirm_depth", "block_states")}, sort_keys=True))
    print("  ledger blocks:", ledger_blocks())
    ev = [json.loads(x) for x in open(L.EVENTS)] if os.path.exists(L.EVENTS) else []
    print("  events:", json.dumps([{k: v for k, v in e.items() if k not in ("ts", "code_hash")} for e in ev]))
    open(L.EVENTS, "w").close()


fails = []


def check(name, cond):
    print(f"  ASSERT {name}: {'OK' if cond else 'FAIL'}")
    if not cond:
        fails.append(name)


print("listener code_hash:", L.CODE_HASH)
L.CONFIRM_DEPTH = 0
st = L.migrate_state({"start_ts": 0, "next_block": 255, "fills": 0})  # last_processed=254
use([Node("A", 256, 255, 256), Node("B", 256, 255, 255), Node("C", 256, 255, 255)])
r1 = L.poll_once(st, None)
show("ROUND 1  head=256, A has logs up to 255 (256 empty), B/C not synced to 256", st, r1)
check("primary getLogs(255,256) issued", any(c.startswith("A eth_getLogs(255,256)") for c in LAST))
check("block 255 state == DATA_PRESENT", r1["states"].get(255) == "DATA_PRESENT")
check("block 256 state == UNAVAILABLE", r1["states"].get(256) == "UNAVAILABLE")
check("last_processed == 255 (cursor NOT advanced past 256)", st["last_processed"] == 255)
check("pending_tip == 256", st["pending_tip"] == 256)
check("256 not written", 256 not in ledger_blocks() and 255 in ledger_blocks())

use([Node("A", 256, 256, 256), Node("B", 256, 256, 256), Node("C", 256, 256, 256)])
r2 = L.poll_once(st, None)
show("ROUND 2  A now has 256", st, r2)
check("block 256 state == DATA_PRESENT", r2["states"].get(256) == "DATA_PRESENT")
check("256 written to ledger", 256 in ledger_blocks())
check("last_processed == 256", st["last_processed"] == 256)
check("round 2 issued getLogs(256,256)", any("eth_getLogs(256,256)" in c for c in LAST))
rows = []
L.close_ledger()
for f in os.listdir(TMP):
    if f.startswith("chain_listener_2"):
        rows += [ (json.loads(x)["tx"], json.loads(x)["log_index"]) for x in gzip.open(os.path.join(TMP, f), "rt")]
check("ledger rows unique (255 not doubled)", len(rows) == len(set(rows)) == 2)

print("=== side checks (informational, not part of T4 pass/fail)")
# S1: default depth 10 never reads the tip
for f in os.listdir(TMP):
    os.remove(os.path.join(TMP, f))
L.CONFIRM_DEPTH = 10
st = L.migrate_state({"start_ts": 0, "next_block": 255, "fills": 0})
use([Node("A", 256, 255, 256), Node("B", 256, 255, 255), Node("C", 256, 255, 255)])
r = L.poll_once(st, None)
show("S1 CONFIRM_DEPTH=10 head=256", st, r)
print("  S1 result: skipped=%s last_processed=%s pending_tip=%s" % (r["skipped"], st["last_processed"], st["pending_tip"]))
# S2: depth 0 and ALL three nodes answer [] for 256 (none of them has it yet)
for f in os.listdir(TMP):
    os.remove(os.path.join(TMP, f))
L.CONFIRM_DEPTH = 0
st = L.migrate_state({"start_ts": 0, "next_block": 255, "fills": 0})
use([Node("A", 256, 255, 256), Node("B", 256, 255, 256), Node("C", 256, 255, 256)])
r = L.poll_once(st, None)
show("S2 CONFIRM_DEPTH=0, all nodes return [] for 256", st, r)
print("  S2 result: 256 state=%s last_processed=%s  (known limit: two nodes agreeing on [] is taken as CONFIRMED_EMPTY; CONFIRM_DEPTH>0 is the protection)" % (r["states"].get(256), st["last_processed"]))

print("T4 RESULT:", "PASS" if not fails else "FAIL " + ", ".join(fails))
sys.exit(1 if fails else 0)
