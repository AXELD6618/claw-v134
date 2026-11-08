import pandas as pd, glob, os, json

BASE = r"E:\WorkBuddy_dot_workbuddy\Claw"
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 60)

# ---- 1) first_board_events_sample.csv ----
f = os.path.join(BASE, "backtest_cache", "first_board_events_sample.csv")
print("="*70)
print("first_board_events_sample.csv")
print("="*70)
df = pd.read_csv(f)
print("shape:", df.shape)
print("columns:", list(df.columns))
print("\ndtypes:\n", df.dtypes)
print("\nhead(3):\n", df.head(3).to_string())
if "t1_zt" in df.columns:
    print("\nt1_zt base rate:", df["t1_zt"].mean(), " counts:", df["t1_zt"].value_counts().to_dict())
if "date" in df.columns:
    print("date range:", df["date"].min(), "->", df["date"].max(), " n_unique dates:", df["date"].nunique())
# key feature distributions
for col in ["vol_ratio","pos_rank","yizi","qiangchou","broke","vol_bin","t1_gap"]:
    if col in df.columns:
        print(f"\n{col}: unique={df[col].nunique()} sample={sorted(df[col].dropna().unique())[:12]}")

# ---- 2) intel gz files ----
print("\n"+"="*70)
print("intel gz files")
print("="*70)
intel_dir = os.path.join(BASE, "backtest_cache", "intel")
gz = sorted(glob.glob(os.path.join(intel_dir, "*.csv.gz")))
print("files:", [os.path.basename(x) for x in gz])
for g in gz[:6]:
    d = pd.read_csv(g)
    print(f"\n-- {os.path.basename(g)} shape={d.shape}")
    print("   cols:", list(d.columns))
    print("   head:\n", d.head(2).to_string())

# ---- 3) board forward files ----
print("\n"+"="*70)
print("board forward files")
print("="*70)
fwd = sorted(glob.glob(os.path.join(intel_dir, "_fwd", "board_*.csv.gz")))
print("n fwd files:", len(fwd), "sample:", [os.path.basename(x) for x in fwd[:3]])
if fwd:
    d = pd.read_csv(fwd[-1])
    print("cols:", list(d.columns))
    print(d.head(2).to_string())
print("\nDONE")
