"""Paired block bootstrap (by init date) of score differences between two models on the same cases."""
import numpy as np, pandas as pd, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from score import cont
rng = np.random.default_rng(7); B = 1000
pt = pd.read_parquet(os.path.join(HERE, "data", "pairs_t.parquet")); pp = pd.read_parquet(os.path.join(HERE, "data", "pairs_p.parquet"))
for d in (pt, pp): d["day"] = np.ceil(d.lead / 24).astype(int)
key = ["sid", "init", "valid"]
PAIRS = [("dwd_icon", "ecmwf_ifs"), ("dwd_icon", "ukmo_global_deterministic_10km"), ("dwd_icon", "ecmwf_aifs025_single"),
         ("dwd_icon", "ncep_gfs013"), ("ecmwf_aifs025_single", "ecmwf_ifs")]
rows = []
for lo, hi in [(1, 3), (4, 7)]:
    for a, b in PAIRS:
        for var in ["t2m", "wind"]:
            d = pt[pt.day.between(lo, hi) & pt.model.isin([a, b])].dropna(subset=[f"{var}_o"])
            w = d.pivot_table(index=key, columns="model", values=f"{var}_f", aggfunc="first").dropna()
            o = d.drop_duplicates(key).set_index(key)[f"{var}_o"].reindex(w.index)
            diff = (w[a] - o).abs() - (w[b] - o).abs()
            g = diff.groupby(level="init").agg(["sum", "count"])
            bs = [g.iloc[s]["sum"].sum() / g.iloc[s]["count"].sum() for s in (rng.choice(len(g), len(g)) for _ in range(B))]
            rows.append(dict(window=f"D{lo}-{hi}", metric=f"{var} MAE diff (a-b, <0 → a better)", a=a, b=b, est=diff.mean(), lo=np.quantile(bs, .05), hi=np.quantile(bs, .95), n=len(diff)))
        d = pp[(pp.win == 12) & pp.day.between(lo, hi) & pp.model.isin([a, b])].dropna(subset=["p_o"])
        w = d.pivot_table(index=key, columns="model", values="p_f", aggfunc="first").dropna()
        o = d.drop_duplicates(key).set_index(key)["p_o"].reindex(w.index)
        inits = w.index.get_level_values("init"); ui = np.array(sorted(set(inits)))
        pos = {u: np.where(inits == u)[0] for u in ui}
        fa, fb, ov = w[a].values, w[b].values, o.values
        est = cont(fa, ov, 1)["ETS"] - cont(fb, ov, 1)["ETS"]; bs = []
        for _ in range(B):
            s = np.concatenate([pos[u] for u in rng.choice(ui, len(ui))])
            bs.append(cont(fa[s], ov[s], 1)["ETS"] - cont(fb[s], ov[s], 1)["ETS"])
        rows.append(dict(window=f"D{lo}-{hi}", metric="ETS12>=1 diff (a-b, >0 → a better)", a=a, b=b, est=est, lo=np.quantile(bs, .05), hi=np.quantile(bs, .95), n=len(ov)))
r = pd.DataFrame(rows); r.to_csv(os.path.join(HERE, "results", "paired_differences.csv"), index=False)
pd.set_option("display.width", 220); print(r.round(3).to_string(index=False))
