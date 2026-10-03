import pandas as pd, numpy as np, json, glob, re, time, duckdb, lightgbm as lgb, sys
sys.path.insert(0,"/work/temp/copy"); import gate as G
R={}; rng=np.random.default_rng(0)
f=pd.read_parquet("fills.parquet")
HL=5.0  # latency > 5s = catch-up / redeploy segment
# ---------- integrity + latency ----------
lat=f.latency; norm=f[f.latency<=HL]
q=lambda s:{k:round(float(s.quantile(v)),3) for k,v in [("p50",.5),("p90",.9),("p95",.95),("p99",.99),("max",1)]}
R["integrity"]={"fills":len(f),"wallets":int(f.wallet.nunique()),"tokens":int(f.token.nunique()),"usdc":round(float(f.usdc.sum()),0),
  "buy":int((f.side=="BUY").sum()),"sell":int((f.side=="SELL").sum()),"code_hashes":f.input_hash.size and 1,
  "hours":int(pd.to_datetime(f.block_ts,unit="s").dt.floor("h").nunique()),"min_fills_hour":int(pd.to_datetime(f.block_ts,unit="s").dt.floor("h").value_counts().min())}
R["latency_all"]=q(lat); R["latency_normal"]=q(norm.latency); R["latency_normal_n"]=len(norm)
R["hl_fills"]=int((lat>HL).sum())
hl=f[lat>HL]; R["hl_windows"]=[[int(a),int(b)] for a,b in hl.groupby((hl.block_ts.diff()>120).cumsum()).block_ts.agg(["min","max"]).values]
# ---------- global re-pull ----------
g=pd.concat([pd.read_parquet(x) for x in glob.glob("gl/*.parquet")],ignore_index=True)
R["global"]={"fills":len(g),"blocks":[int(g.block.min()),int(g.block.max())],"distinct_blocks":int(g.block.nunique())}
W=set(json.load(open("/work/chain_listener_v2/wallets.json"))); W={w.lower() for w in W}
gk=set(zip(g.block,g.li)); fk=set(zip(f.block,f.li))
gw=g[g.maker.str.lower().isin(W)&(g.block<=f.block.max())]; gwk=set(zip(gw.block,gw.li))
R["completeness"]={"ledger_in_chain":round(len(fk&gk)/len(fk),6),"chain_tracked_fills":len(gwk),"missed_by_listener":len(gwk-fk),"extra_in_ledger":len(fk-gk)}
# ---------- signals ----------
b=f[f.side=="BUY"]
s=b.groupby(["wallet","tx","token"],as_index=False).agg(T=("block_ts","min"),block=("block","min"),usdc=("usdc","sum"),shares=("shares","sum"),det=("detect_ts","min"),lat=("latency","max"),nfill=("li","size"))
s["px"]=s.usdc/s.shares; s["hl"]=s.lat>HL
tok=json.load(open("tokens.json"))
s["cid"]=s.token.map(lambda t:(tok.get(t) or {}).get("cid"))
s["slug"]=s.token.map(lambda t:(tok.get(t) or {}).get("slug") or "")
s["q"]=s.token.map(lambda t:(tok.get(t) or {}).get("q") or "")
def settle(t):
    m=tok.get(t)
    if not m or m["px"] is None: return (np.nan,np.nan,np.nan,np.nan)
    st=m["closed"] and m["uma"]=="resolved" and m["px"] in (0.0,1.0)
    ct=pd.Timestamp(m["closedTime"]).timestamp() if m.get("closedTime") else np.nan
    end=pd.Timestamp(m["end"]).timestamp() if m.get("end") else np.nan
    return (m["px"] if st else np.nan, m["px"], ct, end)
st=pd.DataFrame([settle(t) for t in s.token],columns=["won","mark","closed_ts","end_ts"]); s=pd.concat([s,st],axis=1)
s["settled"]=s.won.notna()
gg=g[(g.usdc>=1)&g.token.isin(set(s.token))][["token","ts","price"]].sort_values("ts")
for D in (1,3,5):
    s[f"x{D}"]=np.maximum(s["T"]+D,np.ceil(s.det)).astype("int64")
    m=pd.merge_asof(s[[f"x{D}","token"]].reset_index().sort_values(f"x{D}"),gg.rename(columns={"ts":f"et{D}","price":f"p{D}"}),left_on=f"x{D}",right_on=f"et{D}",by="token",direction="forward",tolerance=60).set_index("index").sort_index()
    s[f"p{D}"]=m[f"p{D}"]; s[f"et{D}"]=m[f"et{D}"]
    s[f"bnd{D}"]=s[f"p{D}"].isna()|(s[f"p{D}"]<0.05)|(s[f"p{D}"]>0.95)|(s.closed_ts<=s[f"x{D}"])
    s[f"net{D}"]=s.won/(s[f"p{D}"]*1.015)-1          # settled only
    s[f"mnet{D}"]=s.mark/(s[f"p{D}"]*1.015)-1        # settled real + unsettled marked at current price
s["own_net"]=s.won/(s.px*1.015)-1
# ---------- wallet features ----------
c=duckdb.connect("/work/temp/train/train.duckdb",read_only=True)
roi=c.execute("SELECT wallet, sum(pnl)/nullif(sum(buy_usdc),0) roi FROM rows WHERE NOT inv_gap GROUP BY 1").df().set_index("wallet").roi
ft=json.load(open("firsttx.json"))
s["first_ts"]=s.wallet.map(lambda w:ft.get(w) if isinstance(ft.get(w),(int,float)) else np.nan)
s["age_d"]=(s["T"]-s.first_ts)/86400; s["roi90"]=s.wallet.map(roi)
s=s.sort_values(["wallet","token","T"]).reset_index(drop=True)
cnt=np.zeros(len(s),int)
for _,idx in s.groupby(["wallet","token"]).indices.items():
    t=s["T"].values[idx]; cnt[idx]=np.arange(len(t))-np.searchsorted(t,t-60)+1
s["burst_n"]=cnt
def ci(v,cl):
    d=pd.DataFrame({"v":v,"c":cl}).dropna()
    if len(d)<30: return None
    a=d.groupby("c").v.agg(["sum","count"]); k=len(a); S=a["sum"].values; N=a["count"].values
    bs=[]
    for _ in range(400):
        i=rng.integers(0,k,k); bs.append(S[i].sum()/N[i].sum())
    return [round(float(np.percentile(bs,2.5))*100,2),round(float(np.percentile(bs,97.5))*100,2)]
def summ(x,tag=None):
    o={"n":len(x),"settled":int(x.settled.sum()),"markets":int(x.cid.nunique())}
    for D in (1,3,5):
        ok=~x[f"bnd{D}"]; e=x[ok&x.settled]; em=x[ok&x[f"mnet{D}"].notna()]
        o[f"d{D}"]={"exec_ok":int(ok.sum()),"settled_n":len(e),"net_settled_pct":round(float(e[f"net{D}"].mean()*100),2) if len(e) else None,
          "ci":ci(e[f"net{D}"].values,e.cid.values),"win":round(float(e.won.mean()*100),1) if len(e) else None,
          "slip_pct":round(float(((e[f"p{D}"]-e.px)/e.px).mean()*100),2) if len(e) else None,
          "n_marked":len(em),"net_with_mark_pct":round(float(em[f"mnet{D}"].mean()*100),2) if len(em) else None}
    e=x[x.settled&(x.px>=0.05)&(x.px<=0.95)]; o["source_own_net_pct"]=round(float(e.own_net.mean()*100),2) if len(e) else None
    return o
main=s[~s.hl]
R["copy_all"]=summ(main); R["copy_hl_segment"]=summ(s[s.hl])
R["copy_usd_weighted_d1"]=(lambda e:round(float((e.net1*e.usdc).sum()/e.usdc.sum()*100),2))(main[~main.bnd1&main.settled])
top_w=main[~main.bnd1&main.settled].wallet.value_counts()
R["concentration"]={"top1_wallet_share":round(float(top_w.iloc[0]/top_w.sum()),3),"top10_share":round(float(top_w.iloc[:10].sum()/top_w.sum()),3),"wallets":int(len(top_w))}
R["copy_wallet_equal_d1"]=round(float(main[~main.bnd1&main.settled].groupby("wallet").net1.mean().mean()*100),2)
R["groups"]={
 "new_lt7d":summ(main[main.age_d<7]),
 "old_gt30d_roi_pos":summ(main[(main.age_d>30)&(main.roi90>0)]),
 "whale_gt10k":summ(main[main.usdc>10000]),
 "burst_gt3_in_60s":summ(main[main.burst_n>3]),
 "age_unknown":int(main.age_d.isna().sum())}
# category split
def cat(sl):
    if re.search(r"bitcoin|ethereum|solana|xrp|btc|eth-|dogecoin|bnb|hype|up-or-down|crypto",sl): return "crypto"
    if re.search(r"-20[0-9]{2}-[0-9]{2}-[0-9]{2}",sl) or re.match(r"(nfl|nba|mlb|nhl|wnba|epl|lal|sea|bun|fl1|ucl|uel|mls|cfb|cbb|atp|wta|bra|arg|mex|fif|kbo|npb|lol|cs2|dota|val|ufc|ipl|cric|tur|por|ned|jpn|kor|chn|aus|nor|den|swe)-",sl): return "sports"
    if re.search(r"election|trump|fed|president|senate|rate",sl): return "politics_macro"
    return "other"
s["cat"]=s.slug.map(cat); main=s[~s.hl]
R["by_cat"]={k:summ(v) for k,v in main.groupby("cat")}
# ---------- model top10 ----------
pr=c.execute("""SELECT wallet, count(*) p_n, avg(label) p_win, sum(pnl) p_pnl, sum(pnl)/nullif(sum(buy_usdc),0) p_roi, avg(first_price) p_avgpx,
  avg((category='crypto')::INT) p_crypto, median(hrs_to_res) p_hold FROM rows WHERE NOT inv_gap AND ct < TIMESTAMP '2026-10-01' GROUP BY 1""").df().set_index("wallet")
fe=s.sort_values("T").groupby(["wallet","cid"],as_index=False).head(1).copy()
fe=fe.join(pr,on="wallet")
fe["first_price"]=fe.px; fe["buy"]=1; fe["lusd"]=np.log1p(fe.usdc); fe["hrs_to_res"]=(fe.end_ts-fe["T"])/3600
fe["catn"]=fe.cat.map({"crypto":0,"sports":1,"politics_macro":2}).fillna(3)
F=["first_price","buy","lusd","hrs_to_res","catn","p_n","p_win","p_pnl","p_roi","p_avgpx","p_crypto","p_hold"]
X=fe[F].astype(float).values; mdl=lgb.Booster(model_file="/work/temp/train/model_v1_roi.txt")
fe["score"]=mdl.predict(X)
thr=float(pd.read_parquet("/work/temp/copy/val_pred.parquet").score.quantile(0.9))
ms=[]
for i in range(min(2000,len(X))):
    t=time.perf_counter(); mdl.predict(X[i:i+1]); ms.append((time.perf_counter()-t)*1000)
R["model"]={"first_entries":len(fe),"feature_prior_missing":int(fe.p_n.isna().sum()),"sept_thr":thr,"top10_n":int((fe.score>=thr).sum()),
  "infer_ms":{"mean":round(float(np.mean(ms)),3),"p99":round(float(np.percentile(ms,99)),3),"max":round(float(np.max(ms)),3)}}
fe["top10"]=fe.score>=thr
R["copy_first_entries"]=summ(fe[~fe.hl]); R["copy_model_top10"]=summ(fe[~fe.hl&fe.top10])
# ---------- CAL-001 joint validation ----------
def joint(x,ms_mean,mode):
    rows=[]
    for D in (1,3,5):
        lat_=(x[f"x{D}"]-x["T"]).astype(float); bnd=x[f"bnd{D}"]
        net=(x[f"net{D}"] if mode=="settled" else x[f"mnet{D}"]).where(x[f"p{D}"].notna())
        rows.append(pd.DataFrame({"D":D,"lat":lat_,"bnd":bnd,"net":net,"cid":x.cid,"veto_a1":lat_>60,"seg":x.hl}))
    d=pd.concat(rows,ignore_index=True)
    if mode=="settled": d=d[x.settled.tolist()*3] if False else d[np.tile(x.settled.values,3)]
    d["veto"]=d.bnd|d.veto_a1|(ms_mean>5.0)
    d["bad"]=d.net.isna()|(d.net<0)
    v,bd=d.veto,d.bad; a=d[~v]
    return {"decisions":len(d),"veto":int(v.sum()),"act":int((~v).sum()),"拦截率":round(float((v&bd).sum()/max(bd.sum(),1)),4),
      "误报率":round(float((v&~bd).sum()/max(v.sum(),1)),4),"漏报率":round(float((~v&bd).sum()/max((~v).sum(),1)),4),
      "veto_A1":int((d.veto_a1&~d.bnd).sum()),"veto_BOUNDARY":int(d.bnd.sum()),
      "ACT_net_pct":round(float(a.net.mean()*100),2) if len(a) else None,"ACT_ci":ci(a.net.values,a.cid.values),
      "NO_GATE_net_pct":round(float(d.net.mean()*100),2),
      "by_D":{int(k):round(float(gq[~gq.veto].net.mean()*100),2) for k,gq in d.groupby("D")}}
mm=R["model"]["infer_ms"]["mean"]
R["joint"]={"all_signals_settled":joint(main,0.0,"settled"),"all_signals_marked":joint(main,0.0,"mark"),
  "top10_settled":joint(fe[~fe.hl&fe.top10],mm,"settled"),"top10_marked":joint(fe[~fe.hl&fe.top10],mm,"mark"),
  "hl_segment_settled":joint(s[s.hl],0.0,"settled")}
# parity: real gate.py on a sample
smp=main.sample(min(3000,len(main)),random_state=1); par=0
for r in smp.itertuples():
    st_={"latency":float(r.x1-r.T),"max_latency":60.0,"causal_trace_available":True,"compute_usage":0.0,"compute_limit":5.0,"boundary_violation":bool(r.bnd1)}
    gd=G.gate(st_,{"ACT_SCORE":0.5})["decision"]; par+=(gd=="VETO")==(bool(r.bnd1) or (r.x1-r.T)>60)
R["gate_parity"]=f"{par}/{len(smp)}"
# highlights
hi=main[main.settled&~main.bnd1].copy(); hi["pnl1"]=hi.usdc*hi.net1
R["top_copy_wins"]=hi.nlargest(8,"pnl1")[["wallet","q","px","p1","usdc","won","net1"]].round(4).to_dict("records")
R["top_whales"]=main.nlargest(8,"usdc")[["wallet","q","px","p1","usdc","won","mark","burst_n"]].round(4).to_dict("records")
s.drop(columns=["q"]).to_parquet("signals.parquet")
json.dump(R,open("result.json","w"),indent=1,ensure_ascii=False,default=lambda o: o.item() if hasattr(o,"item") else str(o))
print(json.dumps(R,ensure_ascii=False,default=str)[:6000])
