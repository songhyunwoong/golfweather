"""Block bootstrap (resampling forecast init dates) for D1-3 and D4-7 window scores -> 90% intervals."""
import numpy as np, pandas as pd, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from score import NAMES, add_blend, homogeneous, cont
rng = np.random.default_rng(42); B = 500
pt = pd.read_parquet(os.path.join(HERE, "data", "pairs_t.parquet")); pp = pd.read_parquet(os.path.join(HERE, "data", "pairs_p.parquet"))
for d in (pt, pp): d["day"] = np.ceil(d.lead / 24).astype(int)
maxday = (pt.groupby("model").lead.max() // 24).astype(int)
pt = pt[pt.day <= pt.model.map(maxday)]; pp = pp[pp.day <= pp.model.map(maxday)]
key = ["sid", "init", "valid"]; out = []
for lo, hi in [(1, 3), (4, 7)]:
    for var in ["t2m", "wind"]:
        d = pt[pt.day.between(lo, hi)][["model", "sid", "init", "valid", "day", f"{var}_f", f"{var}_o"]].dropna(subset=[f"{var}_o"])
        d = add_blend(d, key, [f"{var}_f", f"{var}_o", "day"])
        # models covering the whole window only
        ok = [m for m in d.model.unique() if (m == "BLEND4" and hi <= 7) or maxday.get(m, 0) >= hi]
        d = d[d.model.isin(ok)]; d["day"] = lo  # single homogeneous block across the window
        h = homogeneous(d, key, f"{var}_f"); h["ae"] = (h[f"{var}_f"] - h[f"{var}_o"]).abs()
        g = h.groupby(["model", h.init.dt.date]).ae.agg(["sum", "count"]).reset_index()
        dates = g.init.unique()
        pv = g.pivot(index="init", columns="model", values="sum"); pc = g.pivot(index="init", columns="model", values="count")
        bs = []
        for _ in range(B):
            s = rng.choice(len(dates), len(dates)); bs.append(pv.iloc[s].sum() / pc.iloc[s].sum())
        bs = pd.DataFrame(bs)
        for m in pv.columns:
            out.append(dict(window=f"D{lo}-{hi}", var=f"{var}_MAE", model=m, est=pv[m].sum() / pc[m].sum(), lo=bs[m].quantile(.05), hi=bs[m].quantile(.95)))
    d = pp[(pp.win == 12) & pp.day.between(lo, hi)].dropna(subset=["p_o"])[["model", "sid", "init", "valid", "day", "p_f", "p_o"]]
    d = add_blend(d, key, ["p_f", "p_o", "day"])
    ok = [m for m in d.model.unique() if (m == "BLEND4" and hi <= 7) or maxday.get(m, 0) >= hi]
    d = d[d.model.isin(ok)]; d["day"] = lo
    h = homogeneous(d, key, "p_f"); h["date"] = h.init.dt.date
    dates = np.array(sorted(h.date.unique())); idx = {dd: h[h.date == dd] for dd in dates}
    for m in h.model.unique():
        hm = h[h.model == m]; est = cont(hm.p_f.values, hm.p_o.values, 1.0)["ETS"]
        groups = [g[g.model == m] for g in idx.values()]
        bs = []
        for _ in range(B):
            s = rng.choice(len(groups), len(groups)); x = pd.concat([groups[i] for i in s])
            bs.append(cont(x.p_f.values, x.p_o.values, 1.0)["ETS"])
        out.append(dict(window=f"D{lo}-{hi}", var="ETS12_ge1", model=m, est=est, lo=np.quantile(bs, .05), hi=np.quantile(bs, .95)))
o = pd.DataFrame(out); o.to_csv(os.path.join(HERE, "results", "bootstrap_ci.csv"), index=False)
pd.set_option("display.width", 200)
for (w, v), g in o.groupby(["window", "var"]):
    g = g.sort_values("est", ascending=(v != "ETS12_ge1"))
    print(f"\n{w} {v}"); print(g.assign(model=g.model.map(NAMES)).round(3).to_string(index=False))
