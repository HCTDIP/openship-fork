import json, hashlib, glob, pandas as pd
from report import read_lines, canon
rows=[]; seen=set(); dup=bad=0; codes=set()
for fn in sorted(glob.glob("chain_listener_2*.jsonl.gz")):
    for line in read_lines(fn):
        try: r=json.loads(line)
        except ValueError: continue
        k=(r["tx"],r["log_index"])
        if k in seen: dup+=1; continue
        seen.add(k); oh=r.pop("output_hash")
        if hashlib.sha256(canon(r).encode()).hexdigest()!=oh: bad+=1
        codes.add(r["code_hash"])
        rows.append((r["wallet"],r["taker"],r["side"],r["token_id"],r["shares"],r["usdc"],r["price"],r["block"],r["tx"],r["log_index"],r["block_ts"],r["detect_ts"],r["latency_s"],r["input_hash"],r.get("ts_src","log")))
d=pd.DataFrame(rows,columns="wallet taker side token shares usdc price block tx li block_ts detect_ts latency input_hash ts_src".split())
d.to_parquet("fills.parquet")
print(json.dumps({"n":len(d),"dup":dup,"out_hash_bad":bad,"codes":list(codes),"blocks":[int(d.block.min()),int(d.block.max())],"ts":[int(d.block_ts.min()),int(d.block_ts.max())]}))
