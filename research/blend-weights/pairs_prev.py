"""Past-runs blocks (prev/*.json.gz) + KMA observations -> arrays for blend experiments.
T, W: [model, lead(1..7), station, 6-hourly valid time];  P: 12 h sums [model, lead, station, 00/12 UTC window end]."""
import json, gzip, glob, os, sys, datetime as dt, numpy as np
S = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(S, "..", "..", "pipeline"))
import verify as V
st = json.load(open(os.path.join(S, "stations_obs.json")))
def build(tag, leads):
    files = sorted(glob.glob(os.path.join(S, "data", "prev", f"*_{tag}.json.gz")))
    if not files: return None
    blocks = [json.load(gzip.open(f)) for f in files]
    times = [t for b in blocks for t in b[0]["hourly"]["time"]]
    tt = [dt.datetime.fromisoformat(t).replace(tzinfo=V.UTC) for t in times]
    keys = [k for k in blocks[0][0]["hourly"] if k != "time"]
    models = sorted({k.split("_previous_day")[1][2:] for k in keys})
    get = lambda var, n, m, i: np.array([x if x is not None else np.nan for b in blocks for x in b[i]["hourly"].get(f"{var}_previous_day{n}_{m}", [None] * len(b[i]["hourly"]["time"]))], "float32")
    i6 = [k for k, t in enumerate(tt) if t.hour % 6 == 0]; i12 = [k for k, t in enumerate(tt) if t.hour % 12 == 0 and k >= 11]
    T = np.full((len(models), len(leads), len(st), len(i6)), np.nan, "float32"); W = T.copy(); P = np.full((len(models), len(leads), len(st), len(i12)), np.nan, "float32")
    for a, m in enumerate(models):
        for b_, n in enumerate(leads):
            for i in range(len(st)):
                T[a, b_, i] = get("temperature_2m", n, m, i)[i6]; W[a, b_, i] = get("wind_speed_10m", n, m, i)[i6]
                p = get("precipitation", n, m, i); c = np.concatenate([[0], np.cumsum(p)])      # NaN anywhere in the window -> NaN
                P[a, b_, i] = np.array([c[k + 1] - c[k - 11] for k in i12])
    d0, d1 = tt[0].date() - dt.timedelta(days=1), tt[-1].date() + dt.timedelta(days=1)
    obs = V.load_obs(d0, d1, os.environ.get("KMA_APIHUB_KEY", ""), os.path.join(V.VDIR, "obs_cache"))
    o = lambda k, idx: np.array([[((obs.get(tt[j].astimezone(V.KST), {}).get(s["stn"]) or {}).get(k)) for j in idx] for s in st], dtype=float)
    OP = np.array([[(lambda v: np.nan if v is None else v)(V.obs_rain12(obs, tt[j], s["stn"])) for j in i12] for s in st], dtype=float)
    np.savez_compressed(os.path.join(S, f"pairs_{tag}.npz"), T=T, W=W, P=P, OT=o("t", i6), OW=o("w", i6), OP=OP, models=np.array(models), leads=np.array(list(leads)),
                        t6=np.array([tt[j].isoformat() for j in i6]), t12=np.array([tt[j].isoformat() for j in i12]))
    print(tag, "models", models, "T", T.shape, "P", P.shape, "valid T frac", round(float(np.isfinite(T).mean()), 3), "obs T frac", round(float(np.isfinite(o('t', i6)).mean()), 3), "obs P frac", round(float(np.isfinite(OP).mean()), 3))
build("main", range(1, 8)); build("extra", (1, 3, 5))
