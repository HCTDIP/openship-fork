import json, time, httpx, asyncio, pandas as pd
ws=list(pd.read_parquet("fills.parquet",columns=["wallet"]).wallet.unique())
async def main():
    out={}; sem=asyncio.Semaphore(8)
    async with httpx.AsyncClient(headers={"User-Agent":"Mozilla/5.0"},timeout=30) as h:
        async def one(w):
            async with sem:
                for a in range(4):
                    try:
                        r=await h.get("https://data-api.polymarket.com/activity",params={"user":w,"limit":1,"sortBy":"TIMESTAMP","sortDirection":"ASC"})
                        if r.status_code==200:
                            j=r.json(); out[w]=j[0]["timestamp"] if j else None; return
                    except Exception: pass
                    await asyncio.sleep(2*(a+1))
                out[w]="err"
        await asyncio.gather(*[one(w) for w in ws])
    json.dump(out,open("firsttx.json","w")); print(len(out),sum(v=="err" for v in out.values()))
asyncio.run(main())
