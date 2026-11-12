"""Fetch past forecasts (Open-Meteo Previous Runs API) for the verification stations in 14-day blocks, newest first.
Resumable: existing blocks are skipped. Stops when the API reports its request limit."""
import json, os, subprocess, sys, time, gzip, datetime as dt, urllib.parse
S = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(S, "data", "prev"); os.makedirs(OUT, exist_ok=True)
st = json.load(open(os.path.join(S, "stations_obs.json")))
MAIN = ["icon_global", "ecmwf_ifs", "ecmwf_aifs025_single", "gfs_global", "ukmo_global_deterministic_10km"]
EXTRA = ["ecmwf_ifs025", "jma_gsm", "jma_msm", "meteofrance_arpege_world", "gem_global", "cma_grapes_global"]
VARS = ["temperature_2m", "precipitation", "wind_speed_10m"]
def url(models, leads, a, b):
    q = dict(latitude=",".join(str(s["lat"]) for s in st), longitude=",".join(str(s["lon"]) for s in st), elevation=",".join(str(s["elev"]) for s in st),
             start_date=str(a), end_date=str(b), hourly=",".join(f"{v}_previous_day{n}" for v in VARS for n in leads), models=",".join(models), timezone="UTC", wind_speed_unit="ms")
    key = os.environ.get("OPEN_METEO_API_KEY")       # paid plan: customer host, no pacing needed
    if key: q["apikey"] = key
    return ("https://customer-previous-runs-api.open-meteo.com" if key else "https://previous-runs-api.open-meteo.com") + "/v1/forecast?" + urllib.parse.urlencode(q)
def get(u):
    for a in range(6):
        r = subprocess.run(["curl", "-sS", "-m", "300", "--compressed", u], capture_output=True, text=True)
        try:
            d = json.loads(r.stdout)
            if isinstance(d, dict) and d.get("error"): return d
            return d
        except Exception: time.sleep(8 + 6 * a)
    return {"error": True, "reason": "no response"}
end = dt.date(2026, 11, 6); first = dt.date(2025, 11, 7); pause = 2 if os.environ.get("OPEN_METEO_API_KEY") else (int(sys.argv[1]) if len(sys.argv) > 1 else 185)
while end >= first:
    a = max(first, end - dt.timedelta(days=13))
    for tag, models, leads in (("main", MAIN, range(1, 8)), ("extra", EXTRA, (1, 3, 5))):
        f = os.path.join(OUT, f"{a}_{tag}.json.gz")
        if os.path.exists(f): continue
        d = get(url(models, leads, a, end))
        if isinstance(d, dict) and d.get("error"):
            print("STOP", a, tag, d.get("reason"), flush=True); sys.exit(0 if "limit" in str(d.get("reason", "")).lower() else 1)
        json.dump(d, gzip.open(f, "wt")); print("ok", a, end, tag, len(d), flush=True); time.sleep(pause)
    end = a - dt.timedelta(days=1)
print("ALL DONE", flush=True)
