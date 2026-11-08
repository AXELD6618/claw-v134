import glob, os, json, sys

BASE = r"E:\WorkBuddy_dot_workbuddy\Claw"

# 1) main daily cache
daily = glob.glob(os.path.join(BASE, "backtest_cache", "daily", "*.csv"))
print("backtest_cache/daily count:", len(daily))
if daily:
    import pandas as pd
    df = pd.read_csv(daily[0])
    print("  sample file:", os.path.basename(daily[0]))
    print("  cols:", list(df.columns))
    print(df.head(2).to_string())

# 2) tdx daily cache
tdx = glob.glob(os.path.join(BASE, "backtest_cache_tdx", "daily", "*.csv"))
print("backtest_cache_tdx/daily count:", len(tdx))
if tdx:
    import pandas as pd
    df2 = pd.read_csv(tdx[0])
    print("  tdx cols:", list(df2.columns))

# 3) enrich frag
for f in sorted(glob.glob(os.path.join(BASE, "data", "_enrich_frag_*.json"))):
    try:
        d = json.load(open(f, encoding="utf-8"))
        if isinstance(d, dict):
            k = list(d.keys())
            sample = k[:5]
            print("enrich", os.path.basename(f), "dict keys(first5):", sample, "total", len(k))
        else:
            print("enrich", os.path.basename(f), "type", type(d), "len", len(d))
    except Exception as e:
        print("err", os.path.basename(f), e)

# 4) market snapshot
snap = os.path.join(BASE, "cloud_cache", "market_snapshot_latest.json")
if os.path.exists(snap):
    try:
        d = json.load(open(snap, encoding="utf-8"))
        print("snapshot top keys:", list(d.keys())[:25] if isinstance(d, dict) else type(d))
    except Exception as e:
        print("snap err", e)
else:
    print("no market_snapshot_latest.json")

# 5) alt_data
alt = glob.glob(os.path.join(BASE, "V13_0_data", "alt_data", "*"))
print("alt_data files:", [os.path.basename(x) for x in alt][:25])

# 6) sector/industry mapping anywhere?
print("--- searching for industry/sector mapping files ---")
for pat in ["*industry*", "*sector*", "*board*", "*行业*", "*板块*", "*概念*"]:
    hits = glob.glob(os.path.join(BASE, "**", pat), recursive=True)
    if hits:
        print(pat, "->", [h for h in hits if os.path.isfile(h)][:8])
print("DONE")
