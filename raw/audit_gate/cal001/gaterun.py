import duckdb, numpy as np, pandas as pd, lightgbm as lgb, json, hashlib, time, sys, copy as cp
sys.path.insert(0,"."); import gate as G
src=open("/work/temp/train/train.py").read().split("F_ALL=")[0].replace('duckdb.connect("train.duckdb")','duckdb.connect("/work/temp/train/train.duckdb",read_only=True)')
src=src.replace("SELECT r.wallet, r.src,","SELECT r.wallet, r.src, r.conditionId,"); exec(src)
F=["first_price","buy","lusd","hrs_to_res","cat","p_n","p_win","p_pnl","p_roi","p_avgpx","p_crypto","p_hold"]
va=ds("2026-09-01","2026-10-01","2026-09-01")
X=np.column_stack([np.asarray(va[k],float) for k in F]); key={(w,c):i for i,(w,c) in enumerate(zip(va["wallet"],va["conditionId"]))}
m=lgb.Booster(model_file="/work/temp/train/model_v1_roi.txt")
sha=lambda b: hashlib.sha256(b).hexdigest()
CODE=sha(open("gate.py","rb").read()+open("/work/temp/train/model_v1_roi.txt","rb").read())
smp=pd.read_parquet("sample.parquet"); x=pd.read_parquet("copy_decisions.parquet")
x=x[(x.d>0)&(x.grp!="random_all")].merge(smp[["wallet","conditionId","asset"]],left_on="i",right_index=True)
DEFAULT=dict(max_latency=1.5,compute_limit=440,band=None)
CAL=dict(max_latency=60.0,compute_limit=5.0,band=(0.05,0.95))
rows=[]; inf_ms=[]
for r in x.itertuples():
    j=key[(r.wallet,r.conditionId)]; t=time.perf_counter(); pred=float(m.predict(X[j:j+1])[0]); ms=(time.perf_counter()-t)*1000; inf_ms.append(ms)
    feat_ok = not np.isnan(X[j,5]); price_ok = not np.isnan(r.p)
    net = r.net if r.exec else np.nan
    bad = (not r.exec) or net<0 or not(0.05<=(r.p if price_ok else -1)<=0.95)
    for tag,P in [("DEFAULT",DEFAULT),("CALIBRATED",CAL)]:
        bnd = r.closed or (not price_ok) or (P["band"] is not None and price_ok and not(P["band"][0]<=r.p<=P["band"][1]))
        st={"latency":float(r.d),"max_latency":P["max_latency"],"causal_trace_available":bool(feat_ok and r.asset),"compute_usage":ms,"compute_limit":P["compute_limit"],"boundary_violation":bool(bnd)}
        dec={"ACT_SCORE":min(max(0.5+pred,0.0),1.0)}
        g=G.gate(st,dec)
        rows.append(dict(params=tag,grp=r.grp,delay_s=int(r.d),wallet=r.wallet,condition_id=r.conditionId,asset=r.asset,exec_price=None if not price_ok else float(r.p),
            src_fill=float(r.src_fill),pred_roi=pred,net_1_5pct=None if np.isnan(net) else float(net),bad=bool(bad),gate_decision=g["decision"],gate_reason=g["reason"],all_reasons=g["all_reasons"],
            state=st,gate_evidence=g["gate_evidence"]))
led=open("ledger_copy_backtest.jsonl","w")
for k,r in enumerate(rows):
    inp=json.dumps({"state":r["state"],"wallet":r["wallet"],"cid":r["condition_id"],"delay":r["delay_s"]},sort_keys=True).encode()
    out=json.dumps({"d":r["gate_decision"],"why":r["gate_reason"]},sort_keys=True).encode()
    r.update(seq=k,audit_mode="BACKTEST",input_hash=sha(inp),output_hash=sha(out),code_hash=CODE); led.write(json.dumps(r,ensure_ascii=False)+"\n")
led.close()
d=pd.DataFrame(rows); d["veto"]=d.gate_decision=="VETO"
sc={"inference_ms":{"n":len(inf_ms),"mean":float(np.mean(inf_ms)),"p95":float(np.percentile(inf_ms,95)),"p99":float(np.percentile(inf_ms,99)),"max":float(np.max(inf_ms))},
    "A4_feature_missing":int((~d[d.params=="DEFAULT"].state.apply(lambda s:s["causal_trace_available"])).sum())}
for tag in ["DEFAULT","CALIBRATED"]:
    s=d[d.params==tag]; bad=s.bad; v=s.veto; a=s[~v]
    net_act=a.net_1_5pct.dropna()
    sc[tag]={"decisions":len(s),"veto":int(v.sum()),"act":int((~v).sum()),
      "拦截率":round(float((v&bad).sum()/bad.sum()),4),"误报率":round(float((v&~bad).sum()/max(v.sum(),1)),4),"漏报率":round(float((~v&bad).sum()/max((~v).sum(),1)),4),
      "reason_dist":s[v].gate_reason.value_counts().to_dict(),
      "ACT_net_mean_pct":round(float(net_act.mean()*100),2) if len(net_act) else None,"ACT_n_executed":int(len(net_act)),
      "by_delay":{int(k):{"veto":int(g.veto.sum()),"act":int((~g.veto).sum())} for k,g in s.groupby("delay_s")}}
allnet=d[(d.params=="DEFAULT")].net_1_5pct.dropna(); sc["NO_GATE_all_executed_net_mean_pct"]=round(float(allnet.mean()*100),2); sc["NO_GATE_n"]=int(len(allnet))
json.dump(sc,open("scorecard_copy.json","w"),indent=1,ensure_ascii=False); print(json.dumps(sc,indent=1,ensure_ascii=False))
