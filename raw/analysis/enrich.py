import json, time, urllib.parse, urllib.request, pandas as pd, concurrent.futures as cf
d=pd.read_parquet("fills.parquet",columns=["token"])
toks=list(d.token.unique()); print("tokens",len(toks),flush=True)
out={}
def q(batch,closed):
    qs=urllib.parse.urlencode([("clob_token_ids",t) for t in batch]+[("limit","500"),("closed",closed)])
    for a in range(5):
        try: return json.loads(urllib.request.urlopen(urllib.request.Request(f"https://gamma-api.polymarket.com/markets?{qs}",headers={"User-Agent":"Mozilla/5.0"}),timeout=40).read())
        except Exception: time.sleep(2*(a+1))
    return []
for closed in ("true","false"):
    todo=[t for t in toks if t not in out]; bs=[todo[i:i+40] for i in range(0,len(todo),40)]
    with cf.ThreadPoolExecutor(6) as ex:
        for ms in ex.map(lambda b:q(b,closed),bs):
            for m in ms:
                ids=m.get("clobTokenIds"); ids=json.loads(ids) if isinstance(ids,str) else (ids or [])
                op=m.get("outcomePrices"); op=json.loads(op) if isinstance(op,str) else (op or [])
                oc=m.get("outcomes"); oc=json.loads(oc) if isinstance(oc,str) else (oc or [])
                for j,t in enumerate(ids):
                    out[str(t)]={"cid":m.get("conditionId"),"q":(m.get("question") or "")[:120],"outcome":oc[j] if j<len(oc) else None,
                      "px":float(op[j]) if j<len(op) and op[j] not in (None,"") else None,"closed":bool(m.get("closed")),"uma":m.get("umaResolutionStatus"),
                      "end":m.get("endDate"),"closedTime":m.get("closedTime"),"slug":m.get("slug"),"tags":[x.get("slug") for x in (m.get("tags") or [])][:5] if isinstance(m.get("tags"),list) else None,
                      "cat":m.get("category"),"neg":m.get("negRisk"),"last":m.get("lastTradePrice"),"bid":m.get("bestBid"),"ask":m.get("bestAsk")}
    print(closed,len(out),flush=True)
json.dump(out,open("tokens.json","w")); print("cover",len(out)/len(toks))
