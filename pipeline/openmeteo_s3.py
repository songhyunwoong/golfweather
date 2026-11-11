"""Point forecasts read straight from the Open-Meteo open-data bucket (AWS s3://openmeteo, CC BY 4.0).

No API, so no request limits. For every model the newest runs under data_run/{domain}/ are read at the grid
cell of each course (the land cell among the 2x2 neighbours whose terrain height is closest to the course),
then put on one hourly axis:
  - instantaneous values (temperature, wind, cloud, humidity) are interpolated linearly between native steps
  - accumulations (precipitation) are spread evenly over the step they cover; gusts fill the step they cover
  - hours a newer (shorter) run does not reach are filled from the previous runs
"""
import datetime as dt, json, math, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np, omfiles, s3fs
from grid import grid_from_reader

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
BUCKET = "openmeteo"
LAPSE = 0.0065
# key -> (domain, wind as u/v?, humidity variable, has gusts, has weather_code, hours between runs)
MODELS = {
    "icon": dict(domain="dwd_icon", uv=True, hum="relative_humidity_2m", gust=True, wc=True),
    "ifs": dict(domain="ecmwf_ifs", uv=True, hum="dew_point_2m", gust=True, wc=False),
    "aifs": dict(domain="ecmwf_aifs025_single", uv=True, hum="relative_humidity_2m", gust=False, wc=False),
    "gfs": dict(domain="ncep_gfs013", uv=True, hum="relative_humidity_2m", gust=False, wc=False),
    "ukmo": dict(domain="ukmo_global_deterministic_10km", uv=False, hum="relative_humidity_2m", gust=True, wc=False),
}
RUNS_BACK = 4          # newest run plus up to 3 older ones (covers the long 00/12 UTC runs)

_local = threading.local()
def fs():
    if not hasattr(_local, "fs"):
        _local.fs = s3fs.S3FileSystem(anon=True, default_block_size=64 * 1024, skip_instance_cache=True, config_kwargs={"max_pool_connections": 8})
    return _local.fs

def retry(fn, tries=4):
    for a in range(tries):
        try: return fn()
        except FileNotFoundError: raise
        except Exception:
            if a == tries - 1: raise
            time.sleep(1.5 * (a + 1))

def open_om(path):
    return retry(lambda: omfiles.OmFileReader.from_fsspec(fs(), path))

def variables(m):
    v = ["temperature_2m", "precipitation", "cloud_cover", m["hum"]]
    v += ["wind_u_component_10m", "wind_v_component_10m"] if m["uv"] else ["wind_speed_10m", "wind_direction_10m"]
    if m["gust"]: v.append("wind_gusts_10m")
    if m["wc"]: v.append("weather_code")
    return v

# ------------------------------------------------------------------ grid cells
def select_cells(domain, courses, run_path):
    """course id -> {idx:[y,x], z: model terrain height}. Cached in pipeline/cache."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"cells_{domain}.json")
    cells = json.load(open(path)) if os.path.exists(path) else {}
    key = lambda c: f'{c["lat"]:.5f},{c["lon"]:.5f},{c["elevation_m"]}'
    todo = [c for c in courses if cells.get(c["id"], {}).get("key") != key(c)]
    if todo:
        g = grid_from_reader(open_om(f"{run_path}/temperature_2m.om"))
        def one(c):
            h = open_om(f"{BUCKET}/data/{domain}/static/HSURF.om"); best = None
            for idx, clat, clon in g.candidates(c["lat"], c["lon"]):
                z = float(np.asarray(retry(lambda: h[idx[0]:idx[0] + 1, idx[1]:idx[1] + 1])).ravel()[0])
                if z <= -998: continue                                # sea
                s = abs(z - c["elevation_m"])
                if best is None or s < best[0]: best = (s, [int(idx[0]), int(idx[1])], z)
            if best is None:
                idx = g.candidates(c["lat"], c["lon"])[0][0]; best = (0, [int(idx[0]), int(idx[1])], float(c["elevation_m"]))
            return c["id"], dict(idx=best[1], z=round(best[2], 1), key=key(c))
        with ThreadPoolExecutor(max_workers=48) as ex:
            cells.update(dict(ex.map(one, todo)))
        cells = {c["id"]: cells[c["id"]] for c in courses}
        json.dump(cells, open(path, "w"), separators=(",", ":"))
    return {c["id"]: cells[c["id"]] for c in courses}

# ------------------------------------------------------------------ reading runs
def list_runs(domain):
    """Newest completed run first, as (path, init datetime)."""
    j = json.loads(retry(lambda: fs().cat(f"{BUCKET}/data_run/{domain}/latest.json")))
    t0 = dt.datetime.strptime(j["reference_time"][:16], "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc)
    step = 3 if "msm" in domain else 6
    out = []
    for k in range(0, 8):
        t = t0 - dt.timedelta(hours=step * k)
        p = f"{BUCKET}/data_run/{domain}/{t:%Y/%m/%d/%H%M}Z"
        if k == 0 or retry(lambda: fs().exists(p + "/temperature_2m.om")): out.append((p, t))
        if len(out) >= RUNS_BACK: break
    return out

def read_file(path, idxs):
    """(time[s], values[len(idxs), T]) for unique grid cells."""
    r = open_om(path)
    t = np.asarray(retry(lambda: r.get_child_by_name("time").read_array((slice(None),)))).astype("int64")
    vals = np.stack([np.asarray(retry(lambda i=i: r[i[0]:i[0] + 1, i[1]:i[1] + 1, :])).ravel() for i in idxs]).astype("float32")
    return t, vals

def read_model(key, courses, workers=64, log=print):
    m = MODELS[key]; dom = m["domain"]
    runs = list_runs(dom)
    cells = select_cells(dom, courses, runs[0][0])
    uniq = sorted({tuple(c["idx"]) for c in cells.values()}); pos = {u: i for i, u in enumerate(uniq)}
    row = np.array([pos[tuple(cells[c["id"]]["idx"])] for c in courses])
    tasks = [(rp, v, uniq[i:i + 40], i) for rp, _ in runs for v in variables(m) for i in range(0, len(uniq), 40)]
    def job(t):
        rp, v, part, off = t
        try: return (rp, v, off) + read_file(f"{rp}/{v}.om", part)
        except FileNotFoundError: return (rp, v, off, None, None)
    data = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rp, v, off, tt, vals in ex.map(job, tasks):
            if tt is None: continue
            d = data.setdefault((rp, v), dict(time=tt, vals=np.full((len(uniq), len(tt)), np.nan, "float32")))
            d["vals"][off:off + vals.shape[0]] = vals
    log(f"  {key}: runs {[f'{t:%d %HZ}' for _, t in runs]} cells {len(uniq)} files {len(data)}")
    z = np.array([cells[c["id"]]["z"] for c in courses]); elev = np.array([c["elevation_m"] for c in courses], float)
    return dict(runs=runs, data=data, row=row, dz=(z - elev))

# ------------------------------------------------------------------ to the hourly axis
def to_hourly(axis, t, v, kind):
    """axis: hourly epoch seconds [H]; t: native valid times [T]; v: [N, T]. Returns [N, H] with NaN outside the run."""
    out = np.full((v.shape[0], len(axis)), np.nan, "float32")
    if len(t) == 0: return out
    step = np.diff(t, prepend=t[0] - (t[1] - t[0] if len(t) > 1 else 3600))      # seconds covered by each native value
    if kind == "inst":
        ok = (axis >= t[0]) & (axis <= t[-1])
        j = np.clip(np.searchsorted(t, axis[ok], side="right") - 1, 0, len(t) - 2) if len(t) > 1 else np.zeros(ok.sum(), int)
        if len(t) > 1:
            f = ((axis[ok] - t[j]) / (t[j + 1] - t[j])).astype("float32")
            out[:, ok] = v[:, j] * (1 - f) + v[:, j + 1] * f
        else: out[:, ok] = v[:, [0] * ok.sum()]
    else:                                   # value at t covers (t - step, t]
        j = np.searchsorted(t, axis, side="left")
        ok = (j < len(t)); jj = np.clip(j, 0, len(t) - 1)
        ok &= axis > (t[jj] - step[jj])
        if kind == "sum": out[:, ok] = v[:, jj[ok]] / (step[jj[ok]] / 3600.0)
        else: out[:, ok] = v[:, jj[ok]]     # "fill": gusts, weather code
    return out

def assemble(key, raw, axis):
    """dict of hourly arrays [N, H]: t, ap, p, w, wd, g, c, wc (NaN where no forecast)."""
    m = MODELS[key]; row = raw["row"]; N, H = len(row), len(axis)
    def merged(var, kind):
        out = np.full((N, H), np.nan, "float32")
        for rp, _ in raw["runs"]:                         # newest first; older runs only fill gaps
            d = raw["data"].get((rp, var))
            if d is None: continue
            h = to_hourly(axis, d["time"], d["vals"], kind)[row]
            out = np.where(np.isnan(out), h, out)
        return out
    t = merged("temperature_2m", "inst") + (LAPSE * raw["dz"])[:, None]
    p = np.clip(merged("precipitation", "sum"), 0, None)
    c = np.clip(merged("cloud_cover", "inst"), 0, 100)
    if m["uv"]:
        u, v = merged("wind_u_component_10m", "inst"), merged("wind_v_component_10m", "inst")
    else:
        u = np.full((N, H), np.nan, "float32"); v = u.copy()
        for rp, _ in raw["runs"]:                         # interpolate components, not angles
            a, b = raw["data"].get((rp, "wind_speed_10m")), raw["data"].get((rp, "wind_direction_10m"))
            if a is None or b is None: continue
            rad = np.radians(b["vals"])
            uu = to_hourly(axis, a["time"], -a["vals"] * np.sin(rad), "inst")[row]; vv = to_hourly(axis, a["time"], -a["vals"] * np.cos(rad), "inst")[row]
            u = np.where(np.isnan(u), uu, u); v = np.where(np.isnan(v), vv, v)
    w = np.hypot(u, v); wd = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
    hum = merged(m["hum"], "inst")
    if m["hum"] == "dew_point_2m":
        td = hum + (LAPSE * raw["dz"])[:, None]
        e = 6.105 * np.exp(17.27 * td / (237.7 + td))
    else:
        e = np.clip(hum, 0, 100) / 100 * 6.105 * np.exp(17.27 * t / (237.7 + t))
    ap = t + 0.33 * e - 0.70 * w - 4.00                    # Steadman apparent temperature (shade)
    g = merged("wind_gusts_10m", "fill") if m["gust"] else np.full((N, H), np.nan, "float32")
    if m["wc"]: wc = merged("weather_code", "fill")
    else: wc = np.full((N, H), np.nan, "float32")
    derived = np.where(p >= 7.5, 65, np.where(p >= 2.5, 63, np.where(p >= 0.1, 61, np.where(c < 20, 0, np.where(c < 50, 1, np.where(c < 85, 2, 3)))))).astype("float32")
    derived = np.where((p >= 0.1) & (t <= 0.5), np.where(p >= 2.5, 73, 71), derived)
    wc = np.where(np.isnan(wc), np.where(np.isnan(t), np.nan, derived), wc)
    return dict(t=t, ap=ap, p=p, w=w, wd=wd, g=g, c=c, wc=wc)

def fetch(courses, axis_epoch, log=print):
    """-> ({course_id: {model: {var: [..]}}}, {model: init iso}). Models that fail are left out."""
    axis = np.asarray(axis_epoch, "int64"); out = {c["id"]: {} for c in courses}; inits = {}
    rnd = {"t": 1, "ap": 1, "p": 1, "w": 1, "wd": 0, "g": 1, "c": 0, "wc": 0}
    for key in MODELS:
        t0 = time.time()
        try:
            raw = read_model(key, courses, log=log); arr = assemble(key, raw, axis)
        except Exception as e:
            log(f"  warn: {key} failed: {str(e)[:200]}"); continue
        inits[key] = raw["runs"][0][1].strftime("%Y-%m-%dT%H:%MZ")
        lists = {}
        for k, a in arr.items():
            a = np.round(a.astype("float64"), rnd[k]); obj = a.astype(object)
            if rnd[k] == 0: obj = np.where(np.isnan(a), None, np.nan_to_num(a).astype(int).astype(object))
            else: obj[np.isnan(a)] = None
            lists[k] = obj.tolist()
        for i, c in enumerate(courses):
            if any(x is not None for x in lists["t"][i]):
                out[c["id"]][key] = {k: lists[k][i] for k in lists}
        log(f"  {key}: {time.time() - t0:.0f}s")
    return out, inits
