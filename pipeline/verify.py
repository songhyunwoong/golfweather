"""Verify model forecasts against KMA ASOS observations and store per-init-date statistics.

For every init date in the window, the 00 and 12 UTC runs of each model are read at the verification stations
(data/verification/stations.json) from the Open-Meteo open-data bucket and compared with observations from the KMA
API hub (needs KMA_APIHUB_KEY). Only lead days whose observations are complete are scored, so files of recent init
dates grow as time passes. Output: data/verification/daily/{init date}.json with sums that can be added up:
  t_ae, t_e, t_n   2 m temperature abs error, error, count (00/06/12/18 UTC, lapse-rate corrected to station height)
  w_ae, w_e, w_n   10 m wind speed
  a, b, c, d       12 h precipitation >= 1 mm contingency (hit, false alarm, miss, correct negative), windows ending 00/12 UTC

KMA 단기예보 cannot be re-read later (the API serves the last 3 days only), so each run first archives the 05 and
17 KST issues of the last two days at the station grid points (data/verification/kma_fc, needs
DATA_GO_KR_SERVICE_KEY) and scores the archived issues as model "kma".

Usage: python3 verify.py [--days 16] [--start YYYY-MM-DD --end YYYY-MM-DD]
"""
import argparse, datetime as dt, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openmeteo_s3 as om

HERE = os.path.dirname(os.path.abspath(__file__))
VDIR = os.path.join(HERE, "..", "data", "verification")
UTC, KST = dt.timezone.utc, dt.timezone(dt.timedelta(hours=9))
MAX_LEAD_D = 15
OBS_HOURS_KST = (0, 3, 9, 15, 21)          # 00: previous day's rain total; 03/09/15/21 = 18/00/06/12 UTC
WET = 1.0
BLEND = ["icon", "aifs", "ifs", "ukmo"]

# ------------------------------------------------------------------ observations
def fetch_obs_hour(t_kst, key):
    url = f"https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php?tm={t_kst:%Y%m%d%H%M}&stn=0&help=0&authKey={key}"
    for a in range(4):
        r = subprocess.run(["curl", "-sS", "-m", "60", url], capture_output=True)
        rows = {}
        for line in r.stdout.decode("euc-kr", "ignore").splitlines():
            if line.startswith("#") or not line.strip(): continue
            p = line.split()
            if len(p) < 18 or not p[0].isdigit(): continue
            f = lambda x: (None if float(x) <= -9 else float(x))
            rows[int(p[1])] = dict(w=f(p[3]), t=f(p[11]), rn_day=(None if float(p[11]) <= -50 else max(0.0, float(p[16]))))   # -9 = no rain so far
        if rows: return rows
        time.sleep(2 + 2 * a)
    return {}

def load_obs(day0, day1, key, cache_dir):
    """{(kst datetime): {stn: {...}}} for all OBS_HOURS between two KST dates (inclusive). Cached per day on disk."""
    os.makedirs(cache_dir, exist_ok=True); out = {}; todo = []
    d = day0
    while d <= day1:
        f = os.path.join(cache_dir, f"{d:%Y-%m-%d}.json")
        if os.path.exists(f):
            for k, v in json.load(open(f)).items(): out[dt.datetime.fromisoformat(k)] = {int(s): r for s, r in v.items()}
        else: todo.append(d)
        d += dt.timedelta(days=1)
    now = dt.datetime.now(KST)
    def one(d):
        res = {}
        for h in OBS_HOURS_KST:
            t = dt.datetime(d.year, d.month, d.day, h, tzinfo=KST)
            if t > now - dt.timedelta(minutes=40): continue
            res[t] = fetch_obs_hour(t, key)
        return d, res
    with ThreadPoolExecutor(max_workers=6) as ex:
        for d, res in ex.map(one, todo):
            out.update(res)
            if len(res) == len(OBS_HOURS_KST) and all(res.values()):       # only complete days are cached
                json.dump({t.isoformat(): v for t, v in res.items()}, open(os.path.join(cache_dir, f"{d:%Y-%m-%d}.json"), "w"))
    return out

def obs_rain12(obs, end_utc, stn):
    """12 h rain ending at 00 or 12 UTC from the daily running totals (which reset at 00 KST)."""
    e = end_utc.astimezone(KST); g = lambda t: (obs.get(t, {}).get(stn) or {}).get("rn_day")
    if e.hour == 21:                                   # 09 -> 21 KST, same day
        a, b = g(e.replace(hour=9)), g(e)
        return None if a is None or b is None else max(0.0, b - a)
    prev21, mid, now9 = g((e - dt.timedelta(days=1)).replace(hour=21)), g(e.replace(hour=0)), g(e)      # 21 KST -> 09 KST
    return None if None in (prev21, mid, now9) else max(0.0, mid - prev21) + now9

# ------------------------------------------------------------------ forecasts
def read_run(key, init, stations):
    """Hourly arrays [nstations, hours] of t, w, p for one run, or None if the run is not in the archive."""
    m = om.MODELS[key]; rp = f"{om.BUCKET}/data_run/{m['domain']}/{init:%Y/%m/%d/%H%M}Z"
    if not om.retry(lambda: om.fs().exists(rp + "/temperature_2m.om")): return None
    cells = om.select_cells(m["domain"], stations, rp, tag="stn_")
    uniq = sorted({tuple(c["idx"]) for c in cells.values()}); pos = {u: i for i, u in enumerate(uniq)}
    row = np.array([pos[tuple(cells[s["id"]]["idx"])] for s in stations])
    dz = np.array([cells[s["id"]]["z"] - s["elevation_m"] for s in stations])
    axis = np.array([int((init + dt.timedelta(hours=h)).timestamp()) for h in range(0, MAX_LEAD_D * 24 + 1)], "int64")
    names = ["temperature_2m", "precipitation"] + (["wind_u_component_10m", "wind_v_component_10m"] if m["uv"] else ["wind_speed_10m"])
    parts = [uniq[i:i + 7] for i in range(0, len(uniq), 7)]
    def rd(job):
        v, k = job; return v, k, om.read_file(f"{rp}/{v}.om", parts[k])
    raw = {}
    try:
        with ThreadPoolExecutor(max_workers=10) as ex:
            got = list(ex.map(rd, [(v, k) for v in names for k in range(len(parts))]))
    except FileNotFoundError: return None
    for v in names:
        chunks = sorted((k, r) for vv, k, r in got if vv == v)
        raw[v] = (chunks[0][1][0], np.concatenate([r[1] for _, r in chunks]))
    H = lambda v, kind: om.to_hourly(axis, raw[v][0], raw[v][1], kind)[row]
    t = H("temperature_2m", "inst") + (om.LAPSE * dz)[:, None]; p = H("precipitation", "sum")
    w = np.hypot(H("wind_u_component_10m", "inst"), H("wind_v_component_10m", "inst")) if m["uv"] else H("wind_speed_10m", "inst")
    return dict(t=t, w=w, p=p)

def score_run(fc, init, stations, obs, now, acc):
    """Add this run's statistics to acc[region][lead day]. Valid times are 00/06/12/18 UTC whatever the init hour."""
    h0 = (6 - init.hour % 6) % 6 or 6; H = fc["t"].shape[1]
    for i, s in enumerate(stations):
        for reg in (s["region"], "전국"):
            R = acc.setdefault(reg, {})
            for h in range(h0, min(H, MAX_LEAD_D * 24 + 1), 6):
                valid = init + dt.timedelta(hours=h)
                if valid > now - dt.timedelta(hours=1): break
                day = str((h + 23) // 24); c = R.setdefault(day, dict(t_ae=0.0, t_e=0.0, t_n=0, w_ae=0.0, w_e=0.0, w_n=0, a=0, b=0, c=0, d=0))
                o = (obs.get(valid.astimezone(KST), {}).get(s["stn"]) or {})
                for k in ("t", "w"):
                    f = fc[k][i, h]
                    if o.get(k) is not None and not np.isnan(f):
                        e = float(f) - o[k]; c[k + "_ae"] += abs(e); c[k + "_e"] += e; c[k + "_n"] += 1
                if valid.hour % 12 == 0 and h >= 12:
                    po = obs_rain12(obs, valid, s["stn"]); pf = fc["p"][i, h - 11:h + 1]
                    if po is not None and not np.isnan(pf).any():
                        fw, ow = float(pf.sum()) >= WET, po >= WET
                        c["a" if fw and ow else "b" if fw else "c" if ow else "d"] += 1

# ------------------------------------------------------------------ KMA 단기예보 archive
KMA_DIR = os.path.join(VDIR, "kma_fc")
KMA_ISSUES = (5, 17)                        # KST issue hours that are archived and scored
KMA_KEEP_DAYS = 24

def kma_grid(lat, lon):
    sys.path.insert(0, os.path.join(HERE, "..", "data", "golf-courses"))
    from hangul import kma_grid as f
    return f(lat, lon)

def archive_kma(stations, key, now):
    """Save issues of the last two days that are not archived yet; drop old ones."""
    os.makedirs(KMA_DIR, exist_ok=True); today = now.astimezone(KST)
    grids = {s["stn"]: kma_grid(s["lat"], s["lon"]) for s in stations}
    for back in (0, 1, 2):
        d = (today - dt.timedelta(days=back)).date()
        for hh in KMA_ISSUES:
            issue = dt.datetime(d.year, d.month, d.day, hh, tzinfo=KST); path = os.path.join(KMA_DIR, f"{d}_{hh:02d}.json")
            if os.path.exists(path) or issue > today - dt.timedelta(minutes=20) or issue < today - dt.timedelta(hours=60): continue
            def one(g):
                q = f"serviceKey={key}&pageNo=1&numOfRows=2000&dataType=JSON&base_date={d:%Y%m%d}&base_time={hh:02d}00&nx={g[0]}&ny={g[1]}"
                for a in range(3):
                    r = subprocess.run(["curl", "-sS", "-m", "60", "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?" + q], capture_output=True, text=True)
                    try:
                        b = json.loads(r.stdout)["response"]
                        if b["header"]["resultCode"] != "00": return g, None
                        by = {}
                        for it in b["body"]["items"]["item"]: by.setdefault(it["fcstDate"] + it["fcstTime"], {})[it["category"]] = it["fcstValue"]
                        return g, by
                    except Exception: time.sleep(2 + 2 * a)
                return g, None
            with ThreadPoolExecutor(max_workers=6) as ex: got = dict(ex.map(one, sorted(set(grids.values()))))
            if sum(v is not None for v in got.values()) < len(got) * 0.8: print(f"  warn: KMA issue {d} {hh:02d} incomplete, not archived", file=sys.stderr); continue
            def pcp(v):
                if v in ("강수없음", "", None): return 0.0
                if "미만" in v: return 0.5
                try: return float(v.replace("mm", "").split("~")[0].replace("이상", "").strip())
                except ValueError: return None
            times = sorted({t for v in got.values() if v for t in v}); num = lambda x: (None if x in (None, "") else float(x))
            out = dict(issue=issue.isoformat(), times=times, t={}, w={}, p={})
            for stn, g in grids.items():
                by = got.get(g)
                if not by: continue
                out["t"][stn] = [num(by.get(t, {}).get("TMP")) for t in times]; out["w"][stn] = [num(by.get(t, {}).get("WSD")) for t in times]
                out["p"][stn] = [pcp(by.get(t, {}).get("PCP")) if t in by else None for t in times]
            json.dump(out, open(path, "w"), separators=(",", ":")); print(f"  archived KMA issue {d} {hh:02d} KST ({len(out['t'])} stations)", flush=True)
    for f in os.listdir(KMA_DIR):
        if f[:10] < str((today - dt.timedelta(days=KMA_KEEP_DAYS)).date()): os.remove(os.path.join(KMA_DIR, f))

def kma_run(path, stations):
    """(init UTC, hourly arrays from the issue time) for one archived issue. Rain is used only where the forecast is hourly."""
    j = json.load(open(path)); issue = dt.datetime.fromisoformat(j["issue"]); init = issue.astimezone(UTC)
    hrs = [int((dt.datetime.strptime(t, "%Y%m%d%H%M").replace(tzinfo=KST) - issue).total_seconds() // 3600) for t in j["times"]]
    H = max(hrs) + 1; fc = {k: np.full((len(stations), H), np.nan, "float32") for k in ("t", "w", "p")}
    for i, s in enumerate(stations):
        for k in ("t", "w", "p"):
            v = j[k].get(str(s["stn"]))
            if not v: continue
            xs = [(h, x) for h, x in zip(hrs, v) if x is not None and h >= 0]
            for n, (h, x) in enumerate(xs):
                gap = h - xs[n - 1][0] if n else 1
                if k == "p":
                    if gap == 1: fc[k][i, h] = x               # hourly amounts only; 3-hourly tail is left out
                else:
                    fc[k][i, h] = x
                    if n and 1 < gap <= 3:
                        for m in range(1, gap): fc[k][i, h - m] = x + (xs[n - 1][1] - x) * m / gap
    return init, fc

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=16); ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--no-write", action="store_true", help="do not write daily score files (experiments)")
    ap.add_argument("--dump", help="also save 6-hourly forecast/observation arrays per run here (for blend experiments)")
    a = ap.parse_args(); key = os.environ.get("KMA_APIHUB_KEY")
    if not key: sys.exit("KMA_APIHUB_KEY is not set")
    now = dt.datetime.now(UTC); today = now.date()
    end = dt.date.fromisoformat(a.end) if a.end else today - dt.timedelta(days=1)
    start = dt.date.fromisoformat(a.start) if a.start else end - dt.timedelta(days=a.days - 1)
    stations = [dict(s, id=f"stn{s['stn']}", elevation_m=s["elev"]) for s in json.load(open(os.path.join(VDIR, "stations.json")))]
    obs = load_obs(start, min(today + dt.timedelta(days=1), end + dt.timedelta(days=MAX_LEAD_D + 1)), key, os.path.join(VDIR, "obs_cache"))
    print(f"obs hours {len(obs)}; init dates {start}..{end}; stations {len(stations)}", flush=True)
    if os.environ.get("DATA_GO_KR_SERVICE_KEY"): archive_kma(stations, os.environ["DATA_GO_KR_SERVICE_KEY"], now)
    else: print("  note: DATA_GO_KR_SERVICE_KEY not set, KMA forecasts are not archived", file=sys.stderr)
    os.makedirs(os.path.join(VDIR, "daily"), exist_ok=True)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    def one(d):
        out = {}; accs = {}
        for hh in (0, 12):
            init = dt.datetime(d.year, d.month, d.day, hh, tzinfo=UTC); fcs = {}
            for key_m in om.MODELS:
                try: fc = read_run(key_m, init, stations)
                except Exception as e: print(f"  warn {d} {key_m} {hh:02d}Z: {str(e)[:120]}", file=sys.stderr); fc = None
                if fc is None: continue
                fcs[key_m] = fc; score_run(fc, init, stations, obs, now, accs.setdefault(key_m, {}))
            members = [fcs[k] for k in BLEND if k in fcs]
            if len(members) >= 2:                      # same rule as the app: mean temperature and wind, median precipitation
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    stack = lambda k: np.stack([m[k] for m in members]); cnt = (~np.isnan(stack("t"))).sum(axis=0)
                    bl = dict(t=np.where(cnt >= 2, np.nanmean(stack("t"), axis=0), np.nan), w=np.where(cnt >= 2, np.nanmean(stack("w"), axis=0), np.nan),
                              p=np.where(cnt >= 2, np.nanmedian(stack("p"), axis=0), np.nan))
                score_run(bl, init, stations, obs, now, accs.setdefault("blend", {})); fcs["blend"] = bl
            if a.dump and fcs:                         # 6-hourly t, w and 12 h rain totals of every model and of the blend
                hs = list(range(6, MAX_LEAD_D * 24 + 1, 6)); h12 = list(range(12, MAX_LEAD_D * 24 + 1, 12)); os.makedirs(a.dump, exist_ok=True)
                ob = lambda k: np.array([[((obs.get((init + dt.timedelta(hours=h)).astimezone(KST), {}).get(s["stn"]) or {}).get(k)) for h in hs] for s in stations], dtype=float)
                obp = np.array([[(lambda v: np.nan if v is None else v)(obs_rain12(obs, init + dt.timedelta(hours=h), s["stn"])) for h in h12] for s in stations], dtype=float)
                p12 = lambda p: np.stack([p[:, h - 11:h + 1].sum(axis=1) for h in h12], axis=1).astype("float32")     # NaN if any hour is missing
                np.savez_compressed(os.path.join(a.dump, f"{init:%Y%m%d%H}.npz"), obs_t=ob("t"), obs_w=ob("w"), obs_p=obp,
                                    **{f"{k}_{v}": fcs[k][v][:, hs].astype("float32") for k in fcs for v in ("t", "w")}, **{f"{k}_p": p12(fcs[k]["p"]) for k in fcs})
        for hh in KMA_ISSUES:                              # archived KMA issues of this (KST) date
            f = os.path.join(KMA_DIR, f"{d}_{hh:02d}.json")
            if os.path.exists(f):
                init, fc = kma_run(f, stations); score_run(fc, init, stations, obs, now, accs.setdefault("kma", {}))
        out = {k: v for k, v in accs.items() if v}
        return d, out
    done = 0
    with ThreadPoolExecutor(max_workers=int(os.environ.get("WORKERS", "12"))) as ex:
        for d, out in ex.map(one, days):
            if out and not a.no_write: json.dump(dict(init=str(d), wet_mm=WET, models=out), open(os.path.join(VDIR, "daily", f"{d}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
            done += 1
            if done % 5 == 0 or done == len(days): print(f"  {done}/{len(days)}", flush=True)
    print("verify done")

if __name__ == "__main__":
    main()
