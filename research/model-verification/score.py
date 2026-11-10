"""Score models by lead day on a common (homogeneous) sample. Writes results/*.csv and a JSON summary."""
import numpy as np, pandas as pd, os, json, sys
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results"); os.makedirs(OUT, exist_ok=True)
NAMES = {
    "dwd_icon": "DWD ICON (13km→0.125°)", "ecmwf_ifs": "ECMWF IFS HRES 9km", "ecmwf_ifs025": "ECMWF IFS 0.25°",
    "ecmwf_aifs025_single": "ECMWF AIFS 0.25° (AI)", "ncep_gfs013": "NCEP GFS 0.11°", "ncep_aigfs025": "NCEP AIGFS 0.25° (AI)",
    "jma_gsm": "JMA GSM 0.5°", "jma_msm": "JMA MSM 5km", "ukmo_global_deterministic_10km": "UKMO Global 10km",
    "cma_grapes_global": "CMA GRAPES 0.125°", "meteofrance_arpege_world025": "Météo-France ARPEGE 0.25°",
    "cmc_gem_gdps_15km": "CMC GEM 15km", "BLEND4": "Blend (IFS+ICON+UKMO+GFS 평균)",
}
BLEND = ["ecmwf_ifs", "dwd_icon", "ukmo_global_deterministic_10km", "ncep_gfs013"]
REGION = {"KSI0000RKSM": "수도권", "KSI0000RKSS": "수도권", "KSM00047112": "수도권", "KSM00047119": "수도권",
          "KSM00047090": "강원", "KSM00047100": "강원", "KSM00047101": "강원", "KSM00047105": "강원",
          "KSU047127-1": "충청", "KSM00047129": "충청", "KSU047131-1": "충청", "KSM00047135": "충청", "KSM00047145": "충청", "KSM00047177": "충청",
          "KSI0000RKTN": "영남", "KSM00047130": "영남", "KSM00047136": "영남", "KSU047137-1": "영남", "KSM00047138": "영남", "KSM00047155": "영남", "KSM00047159": "영남", "KSM00047162": "영남",
          "KSM00047140": "호남", "KSM00047146": "호남", "KSM00047156": "호남", "KSM00047165": "호남", "KSM00047168": "호남", "KSM00047170": "호남", "KSM00047172": "호남",
          "KSM00047184": "제주", "KSM00047188": "제주", "KSM00047189": "제주"}

def add_blend(df, key, cols):
    w = df.pivot_table(index=key, columns="model", values=cols[0], aggfunc="first")
    have = [m for m in BLEND if m in w.columns]
    b = w[have].dropna()  # blend only where all members exist
    out = pd.DataFrame({cols[0]: b.mean(axis=1)}).reset_index(); out["model"] = "BLEND4"
    obs = df.drop_duplicates(key)[key + cols[1:]]
    return pd.concat([df, out.merge(obs, on=key)], ignore_index=True)

def homogeneous(df, key, val):
    """Keep only cases where every model reaching this lead day has a forecast."""
    out = []
    for d, g in df.groupby("day"):
        models = g.model.unique()
        cnt = g.dropna(subset=[val]).groupby(key).model.nunique()
        ok = cnt[cnt == len(models)].index
        out.append(g.set_index(key).loc[ok].reset_index())
    return pd.concat(out)

def cont(f, o, thr):
    a = ((f >= thr) & (o >= thr)).sum(); b = ((f >= thr) & (o < thr)).sum()
    c = ((f < thr) & (o >= thr)).sum(); d = ((f < thr) & (o < thr)).sum(); n = a + b + c + d
    ar = (a + b) * (a + c) / n if n else np.nan
    return dict(n=int(n), obs_events=int(a + c), POD=a / (a + c) if a + c else np.nan, FAR=b / (a + b) if a + b else np.nan,
                CSI=a / (a + b + c) if a + b + c else np.nan, FBI=(a + b) / (a + c) if a + c else np.nan,
                ETS=(a - ar) / (a + b + c - ar) if (a + b + c - ar) else np.nan, PC=(a + d) / n if n else np.nan)

def persistence(h, var):
    """Reference forecast: the last observation at the same hour of day available at init time."""
    sys.path.insert(0, HERE)
    from obs import load_station
    o = {s: load_station(s)[var] for s in h.sid.unique()}
    lead_days = np.ceil((h.valid - h.init).dt.total_seconds() / 86400).astype(int)
    pt = h.valid - pd.to_timedelta(lead_days * 24, unit="h")
    return np.array([o[s].get(t, np.nan) for s, t in zip(h.sid, pt)])

def main():
    pt = pd.read_parquet(os.path.join(HERE, "data", "pairs_t.parquet")); pp = pd.read_parquet(os.path.join(HERE, "data", "pairs_p.parquet"))
    pt["day"] = np.ceil(pt.lead / 24).astype(int); pp["day"] = np.ceil(pp.lead / 24).astype(int)
    pt["region"] = pt.sid.map(REGION); pp["region"] = pp.sid.map(REGION)
    # a model takes part in lead day d only if it covers the whole day (e.g. ICON 180 h -> D1-D7)
    maxday = (pt.groupby("model").lead.max() // 24).astype(int)
    pt = pt[pt.day <= pt.model.map(maxday)]; pp = pp[pp.day <= pp.model.map(maxday)]
    print("full-coverage lead days per model:", maxday.to_dict())
    key = ["sid", "init", "valid"]
    res = {}
    # ---------------- temperature / wind
    for var in ["t2m", "wind"]:
        d = pt[["model", "sid", "init", "valid", "day", "region", f"{var}_f", f"{var}_o"]].dropna(subset=[f"{var}_o"])
        d = add_blend(d, key, [f"{var}_f", f"{var}_o", "day", "region"])
        h = homogeneous(d, key, f"{var}_f")
        h["err"] = h[f"{var}_f"] - h[f"{var}_o"]
        s = h.groupby(["model", "day"]).err.agg(MAE=lambda e: e.abs().mean(), BIAS="mean", RMSE=lambda e: np.sqrt((e ** 2).mean()), N="size").reset_index()
        base = h.drop_duplicates(key).copy()
        base["pers"] = persistence(base, var)
        base["perr"] = (base.pers - base[f"{var}_o"]).abs()
        pm = base.dropna(subset=["perr"]).groupby("day").perr.mean().rename("MAE_persist")
        s = s.merge(pm, on="day", how="left")
        s["SKILL_vs_persist"] = 1 - s.MAE / s.MAE_persist
        s.to_csv(os.path.join(OUT, f"{var}_by_day.csv"), index=False)
        r = h[h.day <= 3].groupby(["region", "model"]).err.agg(MAE=lambda e: e.abs().mean(), BIAS="mean", N="size").reset_index()
        r.to_csv(os.path.join(OUT, f"{var}_by_region_d1-3.csv"), index=False)
        dt = h[h.valid.dt.hour.isin([0, 6])]  # 09 & 15 KST daytime
        sd = dt.groupby(["model", "day"]).err.agg(MAE=lambda e: e.abs().mean(), BIAS="mean", N="size").reset_index()
        sd.to_csv(os.path.join(OUT, f"{var}_daytime_by_day.csv"), index=False)
        res[var] = s
    # ---------------- precipitation
    rows, rrows = [], []
    for win, thrs in [(12, [1.0, 10.0]), (24, [1.0, 10.0])]:
        d = pp[(pp.win == win)].dropna(subset=["p_o"])[["model", "sid", "init", "valid", "day", "region", "p_f", "p_o"]]
        d = add_blend(d, key, ["p_f", "p_o", "day", "region"])
        h = homogeneous(d, key, "p_f")
        for (m, day), g in h.groupby(["model", "day"]):
            for thr in thrs:
                rows.append(dict(model=m, day=day, win=win, thr=thr, MAE=(g.p_f - g.p_o).abs().mean(), **cont(g.p_f.values, g.p_o.values, thr)))
        if win == 12:
            for (reg, m), g in h[h.day <= 3].groupby(["region", "model"]):
                rrows.append(dict(region=reg, model=m, **cont(g.p_f.values, g.p_o.values, 1.0)))
    pr = pd.DataFrame(rows); pr.to_csv(os.path.join(OUT, "precip_by_day.csv"), index=False)
    pd.DataFrame(rrows).to_csv(os.path.join(OUT, "precip12_ge1_by_region_d1-3.csv"), index=False)
    # ---------------- windowed summary
    windows = [(1, 3), (4, 5), (6, 7), (8, 10), (11, 15)]
    summ = []
    for lo, hi in windows:
        for m in sorted(set(res["t2m"].model)):
            rec = dict(window=f"D{lo}-{hi}", model=m)
            ok = True
            for var in ["t2m", "wind"]:
                x = res[var][(res[var].model == m) & res[var].day.between(lo, hi)]
                if x.day.nunique() < hi - lo + 1: ok = False; break
                rec[f"{var}_MAE"] = x.MAE.mean(); rec[f"{var}_BIAS"] = x.BIAS.mean(); rec[f"{var}_SKILL"] = x.SKILL_vs_persist.mean()
            if not ok: continue
            for win, thr in [(12, 1.0), (24, 1.0), (24, 10.0)]:
                x = pr[(pr.model == m) & (pr.win == win) & (pr.thr == thr) & pr.day.between(lo, hi)]
                rec[f"ETS{win}_{int(thr)}"] = x.ETS.mean(); rec[f"FBI{win}_{int(thr)}"] = x.FBI.mean()
                rec[f"POD{win}_{int(thr)}"] = x.POD.mean(); rec[f"FAR{win}_{int(thr)}"] = x.FAR.mean()
            summ.append(rec)
    sm = pd.DataFrame(summ)
    for w, g in sm.groupby("window"):
        r = (g.t2m_MAE.rank() + g.wind_MAE.rank() + g.ETS12_1.rank(ascending=False) + g.ETS24_10.rank(ascending=False)) / 4
        sm.loc[g.index, "avg_rank"] = r
    sm.to_csv(os.path.join(OUT, "summary_by_window.csv"), index=False)
    # ---------------- console summary
    pd.set_option("display.width", 250)
    for var in ["t2m", "wind"]:
        print(f"\n=== {var} MAE by lead day (homogeneous sample) ===")
        print(res[var].pivot(index="model", columns="day", values="MAE").round(2).rename(index=NAMES).to_string())
        print(res[var].pivot(index="model", columns="day", values="BIAS").round(2).rename(index=NAMES).iloc[:, :7].to_string())
        print("persistence MAE:", res[var].groupby("day").MAE_persist.first().round(2).to_dict())
    for win, thr in [(12, 1.0), (24, 1.0), (24, 10.0)]:
        x = pr[(pr.win == win) & (pr.thr == thr)]
        print(f"\n=== precip {win}h >= {thr} mm  ETS by day ===")
        print(x.pivot(index="model", columns="day", values="ETS").round(3).rename(index=NAMES).to_string())
        print("FBI d1-3:", x[x.day <= 3].groupby("model").FBI.mean().round(2).rename(index=NAMES).to_dict())
        print("obs events per day:", x.groupby("day").obs_events.max().to_dict())

    print("\n=== summary by lead window (avg_rank: mean rank of T MAE, wind MAE, ETS12>=1, ETS24>=10) ===")
    print(sm.assign(model=sm.model.map(NAMES)).sort_values(["window", "avg_rank"]).round(3).to_string(index=False))

if __name__ == "__main__":
    main()
