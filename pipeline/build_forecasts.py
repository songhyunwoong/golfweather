"""Build per-course forecast files for the app.

Sources: Open-Meteo open data on AWS (ICON, ECMWF IFS, ECMWF AIFS, GFS, UKMO; see openmeteo_s3.py) and
KMA 단기예보 (data.go.kr, optional: needs DATA_GO_KR_SERVICE_KEY). `--source api` uses the Open-Meteo API
instead (rate-limited; for comparison only). Output directory (default ../site/data):
  courses.json        slim search index
  meta.json           generation time, model list, latest run times
  fc/{course_id}.json hourly values for every model on one shared time axis (KST)

Usage: python3 build_forecasts.py [--out DIR] [--limit N] [--source s3|api]
"""
import argparse, datetime as dt, json, math, os, statistics, subprocess, sys, time, urllib.parse
import blendrule
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
KST = dt.timezone(dt.timedelta(hours=9))
DAYS = 16                                 # today + 15; ICON and UKMO end after about 7 days, the rest run to 15
VARS = {"t": "temperature_2m", "ap": "apparent_temperature", "p": "precipitation", "w": "wind_speed_10m",
        "wd": "wind_direction_10m", "g": "wind_gusts_10m", "c": "cloud_cover", "wc": "weather_code"}
ROUND = {"t": 1, "ap": 1, "p": 1, "w": 1, "wd": 0, "g": 1, "c": 0, "wc": 0}
# key -> (Open-Meteo model, meta domain, label, native step segments as (from lead hour, step hours))
MODELS = {
    "icon": ("icon_global", "dwd_icon", "DWD ICON", [(0, 1), (78, 3)]),
    "ifs": ("ecmwf_ifs", "ecmwf_ifs", "ECMWF IFS", [(0, 1), (90, 3), (144, 6)]),
    "aifs": ("ecmwf_aifs025_single", "ecmwf_aifs025_single", "ECMWF AIFS", [(0, 6)]),
    "gfs": ("gfs_global", "ncep_gfs013", "NCEP GFS", [(0, 1), (120, 3)]),
    "ukmo": ("ukmo_global_deterministic_10km", "ukmo_global_deterministic_10km", "UKMO", [(0, 1), (54, 3), (144, 6)]),
}
BLEND = ["icon", "aifs", "ifs", "ukmo"]   # see docs/PLAN.md section 3
RAIN_MM = 0.1                             # hourly amount that counts as "rain" in the model vote

def http_json(url, tries=5):
    """GET JSON through curl (honours proxies in every environment this runs in)."""
    for a in range(tries):
        r = subprocess.run(["curl", "-sS", "-m", "90", "--compressed", url], capture_output=True, text=True)
        try:
            return json.loads(r.stdout)
        except Exception:
            if a == tries - 1: raise RuntimeError(f"request failed: {url[:90]}… {r.stdout[:160]} {r.stderr[:160]}")
            time.sleep(2 + 3 * a)

def rnd(v, n):
    if v is None: return None
    return int(round(v)) if n == 0 else round(v, n)

# ---------------------------------------------------------------- Open-Meteo
def fetch_open_meteo(courses):
    out = {}
    for i in range(0, len(courses), 50):
        chunk = courses[i:i + 50]
        q = urllib.parse.urlencode({
            "latitude": ",".join(str(c["lat"]) for c in chunk), "longitude": ",".join(str(c["lon"]) for c in chunk),
            "elevation": ",".join(str(c["elevation_m"]) for c in chunk), "hourly": ",".join(VARS.values()),
            "models": ",".join(m[0] for m in MODELS.values()), "forecast_days": DAYS, "timezone": "Asia/Seoul", "wind_speed_unit": "ms"})
        d = http_json("https://api.open-meteo.com/v1/forecast?" + q)
        if isinstance(d, dict):
            if d.get("error"): raise RuntimeError(d.get("reason"))
            d = [d]
        assert len(d) == len(chunk)
        for c, loc in zip(chunk, d):
            h = loc["hourly"]; rec = {"time": h["time"], "models": {}}
            for key, (om, *_rest) in MODELS.items():
                m = {k: [rnd(v, ROUND[k]) for v in h.get(f"{name}_{om}", [])] for k, name in VARS.items()}
                if any(v is not None for v in m["t"]): rec["models"][key] = m
            out[c["id"]] = rec
        time.sleep(0.4)
    return out

def model_runs():
    runs = {}
    for key, (_om, dom, *_r) in MODELS.items():
        try:
            j = http_json(f"https://api.open-meteo.com/data/{dom}/static/meta.json", tries=3)
            runs[key] = dt.datetime.fromtimestamp(j["last_run_initialisation_time"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        except Exception as e:
            print("warn: no run time for", key, e, file=sys.stderr)
    return runs

# ---------------------------------------------------------------- KMA 단기예보
def kma_base(now):
    """Latest 단기예보 issue (02,05,...,23 KST) that is published (10 min after the hour)."""
    t = now - dt.timedelta(minutes=15)
    h = ((t.hour + 1) // 3) * 3 - 1
    if h < 0: t -= dt.timedelta(days=1); h = 23
    return t.strftime("%Y%m%d"), f"{h:02d}00"

def parse_pcp(v):
    if v in ("강수없음", "", None): return 0.0
    if "미만" in v: return 0.5
    try: return float(v.replace("mm", "").split("~")[0].replace("이상", "").strip())
    except ValueError: return None

SKY_PTY_WC = {1: 61, 2: 68, 3: 71, 4: 80}     # PTY -> WMO code; SKY 1/3/4 -> 0/2/3
def fetch_kma(grids, key):
    base_date, base_time = kma_base(dt.datetime.now(KST))
    def one(g):
        q = urllib.parse.urlencode({"serviceKey": key, "pageNo": 1, "numOfRows": 2000, "dataType": "JSON",
                                    "base_date": base_date, "base_time": base_time, "nx": g[0], "ny": g[1]})
        try:
            b = http_json("https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?" + q, tries=3)["response"]
            if b["header"]["resultCode"] != "00": return g, None
            by = {}
            for it in b["body"]["items"]["item"]:
                by.setdefault(f'{it["fcstDate"][:4]}-{it["fcstDate"][4:6]}-{it["fcstDate"][6:]}T{it["fcstTime"][:2]}:00', {})[it["category"]] = it["fcstValue"]
            return g, by
        except Exception as e:
            print("warn: KMA grid", g, str(e)[:120], file=sys.stderr); return g, None
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = dict(ex.map(one, grids))
    init = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:]}T{base_time[:2]}:00+09:00"
    return res, init

def kma_series(by, times):
    m = {k: [] for k in ("t", "p", "w", "wd", "c", "wc", "pop")}
    for t in times:
        r = by.get(t)
        if not r:
            for k in m: m[k].append(None)
            continue
        f = lambda k: (float(r[k]) if r.get(k) not in (None, "") else None)
        sky, pty = int(f("SKY") or 0), int(f("PTY") or 0)
        m["t"].append(f("TMP")); m["p"].append(parse_pcp(r.get("PCP"))); m["w"].append(f("WSD"))
        m["wd"].append(rnd(f("VEC"), 0)); m["pop"].append(rnd(f("POP"), 0))
        m["c"].append({1: 10, 3: 65, 4: 95}.get(sky)); m["wc"].append(SKY_PTY_WC.get(pty, {1: 0, 3: 2, 4: 3}.get(sky)))
    return m

# ---------------------------------------------------------------- blend
def blend(models, n, lead=None, table=None):
    """Temperature and wind: bias-corrected, skill-weighted mean of blendrule.MEMBERS for the hour's lead day.
    Precipitation: median of BLEND. Other fields: mean of BLEND."""
    src = [models[k] for k in BLEND if k in models]
    tw = [(k, models[k]) for k in blendrule.MEMBERS if k in models]
    voters = [models[k] for k in MODELS if k in models]
    if len(src) < 2 and len(tw) < 2: return None, None
    b = {k: [] for k in VARS}; votes = []; table = table or {}
    for i in range(n):
        col = lambda k, s=src: [m[k][i] for m in s if i < len(m[k]) and m[k][i] is not None]
        L = lead[i] if lead else 1
        def wavg(key, var, shift=True):          # (value, members)
            xs = [(m[key][i], *blendrule.coef(table, var, L, k)) for k, m in tw if i < len(m[key]) and m[key][i] is not None]
            sw = sum(w for _, w, _ in xs)
            return (sum((x - (bb if shift else 0)) * w for x, w, bb in xs) / sw if len(xs) >= 2 and sw > 0 else None), len(xs)
        tv, nt = wavg("t", "t"); wv, _ = wavg("w", "w"); apv, _ = wavg("ap", "t")
        p, g, c = col("p"), col("g"), col("c")
        ok = nt >= 2
        mean = lambda a, r: rnd(sum(a) / len(a), r) if ok and a else None
        wv = None if wv is None else max(0.0, wv); gm = mean(g, 1)
        b["t"].append(rnd(tv, 1) if tv is not None else None); b["ap"].append(rnd(apv, 1) if apv is not None else None)
        b["w"].append(rnd(wv, 1) if wv is not None else None); b["g"].append(max(gm, rnd(wv, 1)) if gm is not None and wv is not None else gm); b["c"].append(mean(c, 0))
        pm = rnd(statistics.median(p), 1) if ok and p else None
        b["p"].append(pm)
        dirs = [(m["wd"][i], m["w"][i]) for _, m in tw if i < len(m["wd"]) and m["wd"][i] is not None and m["w"][i] is not None]
        if ok and dirs:
            x = sum(s * math.sin(math.radians(d)) for d, s in dirs); y = sum(s * math.cos(math.radians(d)) for d, s in dirs)
            b["wd"].append(int(round(math.degrees(math.atan2(x, y)) % 360)) % 360 if (x or y) else dirs[0][0])
        else: b["wd"].append(None)
        pv = col("p", voters); wet = sum(1 for v in pv if v >= RAIN_MM)
        votes.append([wet, len(pv)] if pv else None)
        if not ok: b["wc"].append(None)
        elif pv and wet * 2 > len(pv): b["wc"].append(71 if (b["t"][-1] is not None and b["t"][-1] <= 0) else 61)
        else: b["wc"].append(0 if (b["c"][-1] or 0) < 25 else 2 if b["c"][-1] < 60 else 3)
    return b, votes

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=os.path.join(HERE, "..", "site", "data")); ap.add_argument("--limit", type=int); ap.add_argument("--source", default="s3", choices=["s3", "api"])
    a = ap.parse_args()
    src = json.load(open(os.path.join(HERE, "..", "data", "golf-courses", "courses.json")))
    # nameless areas cannot be searched; a course whose reviewed coordinate disagrees with its address would get the wrong forecast
    courses = [c for c in src["courses"] if c["name"] and "좌표확인" not in c["review"]]
    if a.limit: courses = courses[:a.limit]
    os.makedirs(os.path.join(a.out, "fc"), exist_ok=True)
    now = dt.datetime.now(KST)
    print(f"courses {len(courses)}", flush=True)
    if a.source == "api":
        om = fetch_open_meteo(courses); runs = model_runs()
    else:
        import openmeteo_s3
        day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        axis = [day0 + dt.timedelta(hours=h) for h in range(24 * DAYS)]
        by_course, runs = openmeteo_s3.fetch(courses, [int(t.timestamp()) for t in axis])
        times = [t.strftime("%Y-%m-%dT%H:%M") for t in axis]
        om = {cid: {"time": times, "models": m} for cid, m in by_course.items()}
    kma, kma_init = {}, None
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY")
    if key:
        grids = sorted({(c["kma_nx"], c["kma_ny"]) for c in courses})
        kma, kma_init = fetch_kma(grids, key)
        print(f"KMA grids {sum(v is not None for v in kma.values())}/{len(grids)}", flush=True)
    else:
        print("warn: DATA_GO_KR_SERVICE_KEY not set, skipping KMA", file=sys.stderr)
    n_kma = 0; table = blendrule.load(); lead = None
    for c in courses:
        if lead is None:                         # lead day of every hour, counted from now (hours already past count as day 1)
            t0 = now.replace(tzinfo=None)
            lead = [min(15, max(1, math.ceil((dt.datetime.fromisoformat(t) - t0).total_seconds() / 86400))) for t in om[c["id"]]["time"]]
        rec = om[c["id"]]; times = rec["time"]; models = rec["models"]
        b, votes = blend(models, len(times), lead, table)
        if b: models["blend"] = b
        by = kma.get((c["kma_nx"], c["kma_ny"]))
        if by:
            models["kma"] = kma_series(by, times); n_kma += 1
        out = dict(id=c["id"], generated=now.strftime("%Y-%m-%dT%H:%M+09:00"), time=times, rain_votes=votes, models=models)
        json.dump(out, open(os.path.join(a.out, "fc", c["id"] + ".json"), "w"), ensure_ascii=False, separators=(",", ":"))
    slim = [dict(id=c["id"], name=c["name"], aliases=c["aliases"], cho=c["choseong"], norm=c["name_norm"], sido=c["sido"], sigungu=c["sigungu"],
                 lat=c["lat"], lon=c["lon"], elev=c["elevation_m"], type=c["type"], addr=c["address"], tel=c["tel"]) for c in courses]
    json.dump(dict(version=src["meta"]["version"], courses=slim), open(os.path.join(a.out, "courses.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    info = {k: dict(label=v[2], init=runs.get(k), native_steps=v[3]) for k, v in MODELS.items()}
    info["blend"] = dict(label="블렌드", members=BLEND, tw_members=blendrule.MEMBERS, weighted=bool(table), init=None)
    info["kma"] = dict(label="기상청 단기예보", init=kma_init, courses=n_kma)
    json.dump(dict(generated=now.strftime("%Y-%m-%dT%H:%M+09:00"), source=a.source, courses=len(courses), days=DAYS, rain_mm=RAIN_MM, models=info),
              open(os.path.join(a.out, "meta.json"), "w"), ensure_ascii=False, indent=1)
    missing = {k: sum(1 for c in courses if k not in om[c["id"]]["models"]) for k in list(MODELS) + ["blend"]}
    print("done", dict(courses=len(courses), kma=n_kma, missing=missing), flush=True)
    if any(v > len(courses) * 0.2 for k, v in missing.items() if k != "aifs") or len(courses) == 0:
        sys.exit("too many courses without forecasts — keeping the previous deployment")

if __name__ == "__main__":
    main()
