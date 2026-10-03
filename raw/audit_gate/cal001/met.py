import pandas as pd, json, numpy as np
d=pd.read_json("ledger_copy_backtest.jsonl",lines=True)
d["bad"]=d.net_1_5pct.isna() | (d.net_1_5pct<0)   # non-circular: can't fill, or loses money after 1.5%
d["veto"]=d.gate_decision=="VETO"
out={}
for tag in ["DEFAULT","CALIBRATED"]:
    s=d[d.params==tag]; v=s.veto; b=s.bad
    out[tag]={"n":len(s),"veto":int(v.sum()),"act":int((~v).sum()),"bad_total":int(b.sum()),
      "拦截率":round(float((v&b).sum()/b.sum()),4),"误报率":round(float((v&~b).sum()/max(v.sum(),1)),4),"漏报率":round(float((~v&b).sum()/max((~v).sum(),1)),4),
      "reason_dist":s[v].gate_reason.value_counts().to_dict(),"all_reasons_hits":pd.Series([x for l in s[v].all_reasons for x in l]).value_counts().to_dict(),
      "ACT_net_mean_pct":round(float(s[~v].net_1_5pct.mean()*100),2) if (~v).any() else None,
      "VETO_net_mean_pct(executable ones)":round(float(s[v].net_1_5pct.mean()*100),2),
      "VETO_net_median_pct":round(float(s[v].net_1_5pct.median()*100),2)}
s=d[d.params=="DEFAULT"]; e=s.net_1_5pct.dropna()
out["NO_GATE"]={"executed":len(e),"net_mean_pct":round(e.mean()*100,2),"net_median_pct":round(e.median()*100,2),
  "net_mean_excl_31_price<0.05_wins":round(e[~((s.exec_price<0.05)&(s.net_1_5pct>0)).loc[e.index]].mean()*100,2),
  "n_price<0.05_wins":int(((s.exec_price<0.05)&(s.net_1_5pct>0)).sum())}
c=d[d.params=="CALIBRATED"]
out["CAL_ACT_by_group"]={g:round(float(x[x.gate_decision=="ACT"].net_1_5pct.mean()*100),2) for g,x in c.groupby("grp")}
out["boundary_breakdown"]={"closed_before_exec":int(s.state.apply(lambda z:z["boundary_violation"]).sum()),
  "no_price":int(s.exec_price.isna().sum()),"price_out_of_0.05_0.95":int(((s.exec_price<0.05)|(s.exec_price>0.95)).sum()),
  "by_delay_out_of_band":s.assign(o=(s.exec_price<0.05)|(s.exec_price>0.95)).groupby("delay_s").o.sum().to_dict(),
  "by_delay_closed":s.assign(o=s.state.apply(lambda z:z["boundary_violation"])).groupby("delay_s").o.sum().to_dict()}
out["pred_roi_range"]=[float(d.pred_roi.min()),float(d.pred_roi.max())]
json.dump(out,open("scorecard_copy.json","w"),indent=1,ensure_ascii=False,default=int); print(json.dumps(out,indent=1,ensure_ascii=False,default=int))
