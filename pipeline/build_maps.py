"""Gridded weather over Korea for the map screen.

For each map model, reads the Korea box of the newest runs from the Open-Meteo open-data bucket, puts it on a
3-hourly time axis (now .. +7 days) and regrids to 0.1 deg. Output (default ../site/data/map):
  {model}.json                  grid, times, value encoding
  {model}_{t|p|u|v}.png         all time steps stacked vertically; value = (R*256 + G) * scale + offset, 65535 = no data

Usage: python3 build_maps.py [--out DIR] [--models icon,ifs,gfs]
"""
import argparse, datetime as dt, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openmeteo_s3 as om
from grid import grid_from_reader, RegularGrid

HERE = os.path.dirname(os.path.abspath(__file__))
KST = dt.timezone(dt.timedelta(hours=9))
WEST, EAST, SOUTH, NORTH, STEP = 124.0, 132.0, 32.5, 39.0, 0.1
LONS = np.round(np.arange(WEST, EAST + 1e-6, STEP), 4); LATS = np.round(np.arange(NORTH, SOUTH - 1e-6, -STEP), 4)   # row 0 = north
STEP_H, DAYS = 3, 7
ENC = {"t": (0.01, -60.0), "p": (0.01, 0.0), "u": (0.01, -100.0), "v": (0.01, -100.0)}     # value = raw * scale + offset
VARS = {"t": ("temperature_2m", "inst"), "p": ("precipitation", "sum"), "u": ("wind_u_component_10m", "inst"), "v": ("wind_v_component_10m", "inst")}
LABEL = {"icon": "DWD ICON", "ifs": "ECMWF IFS", "gfs": "NCEP GFS"}

def source_rows(reader):
    """Rows of the native grid covering the box (+ margin): list of (lat, lons[], index slice (y, x0, x1))."""
    g = grid_from_reader(reader); m = 0.5; rows = []
    if isinstance(g, RegularGrid):
        y0 = int(np.floor((SOUTH - m - g.lat0) / g.dlat)); y1 = int(np.ceil((NORTH + m - g.lat0) / g.dlat))
        x0 = int(np.floor((WEST - m - g.lon0) / g.dlon)); x1 = int(np.ceil((EAST + m - g.lon0) / g.dlon)) + 1
        for y in range(y0, y1 + 1): rows.append((g.lat0 + y * g.dlat, g.lon0 + np.arange(x0, x1) * g.dlon, (y, x0, x1)))
    else:                                                   # ECMWF O1280: every latitude row has its own longitude count
        for r in range(len(g.lats)):
            if not (SOUTH - m <= g.lats[r] <= NORTH + m): continue
            dl = 360.0 / g.nlon[r]; x0 = int(np.floor((WEST - m) / dl)); x1 = int(np.ceil((EAST + m) / dl)) + 1
            rows.append((float(g.lats[r]), np.arange(x0, x1) * dl, (0, int(g.offset[r]) + x0, int(g.offset[r]) + x1)))
    return rows

def read_rows(path, rows):
    """-> (time[s], [vals[nlon, T] per row]) or None when the file does not exist."""
    try: r = om.open_om(path)
    except FileNotFoundError: return None
    t = np.asarray(om.retry(lambda: r.get_child_by_name("time").read_array((slice(None),)))).astype("int64")
    out = []
    for _lat, _lons, (y, x0, x1) in rows:
        a = np.asarray(om.retry(lambda: r[y:y + 1, x0:x1, :])).astype("float32")
        out.append(a.reshape(x1 - x0, -1))
    return t, out

def regrid(rows, vals):
    """vals: [N, T] in row order -> [len(LATS), len(LONS), T] by linear interpolation along longitude, then latitude."""
    T = vals.shape[1]; per_row = []; lats = []; i = 0
    for lat, lons, _ in rows:
        n = len(lons); v = vals[i:i + n]; i += n
        f = np.clip((LONS - lons[0]) / (lons[1] - lons[0]), 0, n - 1 - 1e-6); j = np.floor(f).astype(int); w = (f - j)[:, None]
        per_row.append(v[j] * (1 - w) + v[j + 1] * w); lats.append(lat)
    lats = np.array(lats); order = np.argsort(lats); lats = lats[order]; arr = np.stack(per_row)[order]      # [rows, nx, T], south -> north
    k = np.clip(np.searchsorted(lats, LATS, side="right") - 1, 0, len(lats) - 2); w = ((LATS - lats[k]) / (lats[k + 1] - lats[k]))[:, None, None]
    return arr[k] * (1 - w) + arr[k + 1] * w

def build_model(key, axis_h, out_dir, log=print):
    dom = om.MODELS[key]["domain"]; runs = om.list_runs(dom); t0 = time.time()
    rows = source_rows(om.open_om(f"{runs[0][0]}/temperature_2m.om"))
    N = sum(len(r[1]) for r in rows)
    chunks = [rows[i:i + 6] for i in range(0, len(rows), 6)]
    tasks = [(rp, var, ci) for rp, _ in runs for var, _ in VARS.values() for ci in range(len(chunks))]
    def job(t):
        rp, var, ci = t; return rp, var, ci, read_rows(f"{rp}/{var}.om", chunks[ci])
    data = {}
    with ThreadPoolExecutor(max_workers=48) as ex:
        for rp, var, ci, res in ex.map(job, tasks):
            if res is None: continue
            data.setdefault((rp, var), {})[ci] = res
    fields = {}
    for k, (var, kind) in VARS.items():
        merged = np.full((N, len(axis_h)), np.nan, "float32")
        for rp, _ in runs:                                  # newest first; older runs only fill what is missing
            d = data.get((rp, var))
            if not d or len(d) < len(chunks): continue
            tt = d[0][0]; vals = np.concatenate([np.concatenate(d[ci][1]) for ci in range(len(chunks))])
            h = om.to_hourly(axis_h, tt, vals, kind)
            merged = np.where(np.isnan(merged), h, merged)
        fields[k] = merged
    # hourly -> 3-hourly: instantaneous values at the step, precipitation summed over the step
    idx = np.arange(STEP_H - 1, len(axis_h), STEP_H)
    times = [dt.datetime.fromtimestamp(int(axis_h[i]), KST).strftime("%Y-%m-%dT%H:%M") for i in idx]
    out = {}
    for k, a in fields.items():
        if k == "p": s = np.stack([a[:, i - STEP_H + 1:i + 1].sum(axis=1) for i in idx], axis=1)      # NaN if any hour is missing
        else: s = a[:, idx]
        g = regrid(rows, s)                                                                    # [ny, nx, T]
        scale, off = ENC[k]; raw = np.round((g - off) / scale)
        raw = np.where(np.isnan(raw), 65535, np.clip(raw, 0, 65534)).astype(np.uint16)
        img = np.zeros((len(LATS) * len(times), len(LONS), 3), np.uint8)
        stacked = raw.transpose(2, 0, 1).reshape(len(times) * len(LATS), len(LONS))          # time steps stacked top to bottom
        img[..., 0] = stacked >> 8; img[..., 1] = stacked & 255
        Image.fromarray(img, "RGB").save(os.path.join(out_dir, f"{key}_{k}.png"), optimize=True)
        out[k] = dict(file=f"{key}_{k}.png", scale=scale, offset=off, valid=[bool((raw[:, :, i] != 65535).mean() > 0.9) for i in range(len(times))])
    ok = [i for i in range(len(times)) if out["t"]["valid"][i]]
    meta = dict(model=key, label=LABEL.get(key, key), init=runs[0][1].strftime("%Y-%m-%dT%H:%MZ"), west=WEST, east=EAST, south=SOUTH, north=NORTH, step=STEP,
                nx=len(LONS), ny=len(LATS), step_hours=STEP_H, times=times, last_valid=(ok[-1] if ok else -1), vars=out)
    json.dump(meta, open(os.path.join(out_dir, f"{key}.json"), "w"), separators=(",", ":"))
    log(f"  map {key}: cells {N} runs {len(runs)} steps {len(times)} valid to {times[ok[-1]] if ok else None} {time.time() - t0:.0f}s")
    return meta

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=os.path.join(HERE, "..", "site", "data", "map")); ap.add_argument("--models", default="icon,ifs,gfs")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    now = dt.datetime.now(KST); start = now.replace(minute=0, second=0, microsecond=0); start -= dt.timedelta(hours=start.hour % STEP_H)
    # hourly axis so that every 3-hourly step has its 3 preceding hours
    axis_h = np.array([int((start + dt.timedelta(hours=h - (STEP_H - 1))).timestamp()) for h in range(24 * DAYS + STEP_H)], "int64")
    done = []
    for key in a.models.split(","):
        try: build_model(key, axis_h, a.out); done.append(key)
        except Exception as e: print(f"  warn: map {key} failed: {str(e)[:300]}", file=sys.stderr)
    json.dump(dict(generated=now.strftime("%Y-%m-%dT%H:%M+09:00"), models=done), open(os.path.join(a.out, "index.json"), "w"))
    print("maps done", done)
    if not done: sys.exit("no map model could be built")

if __name__ == "__main__":
    main()
