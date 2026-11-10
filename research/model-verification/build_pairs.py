"""Turn extracted per-run point forecasts + GHCNh observations into forecast/observation pairs.
Output: pairs_t.parquet (2 m temperature & 10 m wind at 00/06/12/18 UTC) and pairs_p.parquet (12 h / 24 h precipitation)."""
import numpy as np, pandas as pd, glob, os, json, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from obs import load_station
from stations import load_station_meta
LAPSE = 0.0065  # K/m, standard-atmosphere correction from model terrain height to station height
HERE = os.path.dirname(os.path.abspath(__file__))

def main():
    st = load_station_meta()
    obs = {s: load_station(s) for s in st.sid}
    elev = dict(zip(st.sid, st.elev))
    rows_t, rows_p = [], []
    for f in sorted(glob.glob(os.path.join(HERE, "data", "raw", "*.npz"))):
        base = os.path.basename(f)[:-4]
        model, rest = base.split("_2026_")
        init = pd.Timestamp("2026-" + rest[:5].replace("_", "-") + " " + rest[6:8] + ":00", tz="UTC")
        cells = json.load(open(os.path.join(HERE, "data", "raw", f"cells_{model}.json")))
        zc = np.array([c["z"] for c in cells]); zs = np.array([elev[c["sid"]] for c in cells])
        sids = [c["sid"] for c in cells]
        z = np.load(f)
        # --- temperature & wind at 00/06/12/18 UTC
        tt = pd.to_datetime(z["temperature_2m__time"], unit="s", utc=True)
        T = z["temperature_2m"] + LAPSE * (zc - zs)[:, None]
        if "wind_speed_10m" in z:
            W = z["wind_speed_10m"]; tw = pd.to_datetime(z["wind_speed_10m__time"], unit="s", utc=True)
        else:
            W = np.hypot(z["wind_u_component_10m"], z["wind_v_component_10m"]); tw = pd.to_datetime(z["wind_u_component_10m__time"], unit="s", utc=True)
        wmap = {t: k for k, t in enumerate(tw)}
        for k, t in enumerate(tt):
            lead = (t - init).total_seconds() / 3600
            if lead <= 0 or t.hour % 6: continue
            kw = wmap.get(t)
            for i, s in enumerate(sids):
                o = obs[s]
                if t not in o.index: continue
                rows_t.append((model, init, t, lead, s, float(T[i, k]), float(W[i, kw]) if kw is not None else np.nan,
                               o.at[t, "t2m"], o.at[t, "wind"]))
        # --- precipitation: values are accumulations over the preceding step
        tp = z["precipitation__time"]; P = z["precipitation"]
        lead_h = (tp - init.timestamp()) / 3600.0   # precip time axis starts at the first step, not at init
        step = np.diff(lead_h, prepend=0.0)          # value at t = accumulation over (t - step, t]
        for win in (12, 24):
            for end in np.arange(win, lead_h.max() + 1, win if win == 24 else 12):
                endt = init + pd.Timedelta(hours=float(end))
                if win == 24 and endt.hour != 0: continue
                if win == 12 and endt.hour not in (0, 12): continue
                sel = (lead_h > end - win) & (lead_h <= end)
                if abs(np.nansum(step[sel]) - win) > 1e-6: continue  # steps must tile the window exactly
                acc = np.clip(P[:, sel].sum(axis=1), 0, None)
                ot = endt - pd.Timedelta(hours=1)  # obs windows end at 11/23 UTC
                col = "p12" if win == 12 else "p24"
                for i, s in enumerate(sids):
                    o = obs[s]
                    if col not in o or ot not in o.index: continue
                    rows_p.append((model, init, endt, float(end), win, s, float(acc[i]), o.at[ot, col]))
    pt = pd.DataFrame(rows_t, columns=["model", "init", "valid", "lead", "sid", "t2m_f", "wind_f", "t2m_o", "wind_o"])
    pp = pd.DataFrame(rows_p, columns=["model", "init", "valid", "lead", "win", "sid", "p_f", "p_o"])
    pt.to_parquet(os.path.join(HERE, "data", "pairs_t.parquet")); pp.to_parquet(os.path.join(HERE, "data", "pairs_p.parquet"))
    print(len(pt), len(pp))
    print(pt.groupby("model").size(), pp.groupby(["model", "win"]).size())

if __name__ == "__main__":
    main()
