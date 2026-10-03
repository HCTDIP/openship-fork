"""Re-pull ALL Polymarket OrderFilled logs for [A,B] -> compact parquet chunks (resume-safe)."""
import json, os, sys, time, urllib.request, concurrent.futures as cf
import pandas as pd
TOPIC="0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
EX={"0xe111180000d2663c0091e4f400237545b87b996b","0xe2222d279d744050d28e00520010520000310f59","0xe3333700ca9d93003f00f0f71f8515005f6c00aa"}
RPCS=["https://polygon.drpc.org","https://1rpc.io/matic"]
A,B=int(sys.argv[1]),int(sys.argv[2]); CH=300; STEP=int(os.environ.get("STEP","20"))
os.makedirs("gl",exist_ok=True)
def rpc(u,m,p):
    req=urllib.request.Request(u,data=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),headers={"User-Agent":"Mozilla/5.0","Content-Type":"application/json"})
    r=json.loads(urllib.request.urlopen(req,timeout=40).read())
    if r.get("result") is None: raise RuntimeError(str(r.get("error"))[:200])
    return r["result"]
def get(a,b,k):
    for t in range(12):
        u=RPCS[(k+t)%len(RPCS)]
        try: return rpc(u,"eth_getLogs",[{"fromBlock":hex(a),"toBlock":hex(b),"topics":[TOPIC]}])
        except Exception as e: time.sleep(1+t)
    raise RuntimeError(f"fail {a}-{b}")
tsc={}
def bts(l):
    if l.get("blockTimestamp"): return int(l["blockTimestamp"],16)
    bn=l["blockNumber"]
    if bn not in tsc: tsc[bn]=int(rpc(RPCS[0],"eth_getBlockByNumber",[bn,False])["timestamp"],16)
    return tsc[bn]
def chunk(c0):
    fn=f"gl/{c0}.parquet"
    if os.path.exists(fn): return 0
    rows=[]
    for a in range(c0,min(c0+CH,B+1),STEP):
        b=min(a+STEP-1,c0+CH-1,B)
        for l in get(a,b,a//STEP):
            if len(l.get("topics",[]))<4 or l["address"].lower() not in EX: continue
            d=l["data"][2:]; v=[int(d[i:i+64],16) for i in range(0,256,64)]
            buy=v[0]==0; usdc,sh=(v[2],v[3]) if buy else (v[3],v[2])
            if not sh: continue
            rows.append((str(v[1]),int(l["blockNumber"],16),int(l["logIndex"],16),bts(l),usdc/sh,usdc/1e6,buy,"0x"+l["topics"][2][-40:]))
    pd.DataFrame(rows,columns=["token","block","li","ts","price","usdc","mbuy","maker"]).to_parquet(fn+".tmp"); os.replace(fn+".tmp",fn)
    return len(rows)
starts=list(range(A,B+1,CH)); n=0; t0=time.time()
with cf.ThreadPoolExecutor(int(os.environ.get("W","6"))) as ex:
    for i,r in enumerate(ex.map(chunk,starts)):
        n+=r
        if i%10==0: print(i,len(starts),n,round(time.time()-t0),flush=True)
open("repull.done","w").write(str(n)); print("done",n)
