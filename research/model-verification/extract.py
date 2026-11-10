"""Extract per-run point forecasts from the Open-Meteo open-data archive (AWS s3://openmeteo/data_run)
for Korean verification stations. Output: one .npz per (model, run) with per-variable arrays."""
import omfiles, s3fs, numpy as np, os, sys, json, time, datetime as dt, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(__file__))
from grid import grid_from_reader, RegularGrid
from stations import load_station_meta

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw")
os.makedirs(OUT, exist_ok=True)
UV = ["temperature_2m", "precipitation", "wind_u_component_10m", "wind_v_component_10m"]
SP = ["temperature_2m", "precipitation", "wind_speed_10m"]
MODELS = {
    "dwd_icon": UV, "ecmwf_ifs": UV, "ecmwf_ifs025": UV, "ecmwf_aifs025_single": UV,
    "ncep_gfs013": UV, "ncep_aigfs025": UV, "jma_gsm": UV, "jma_msm": UV,
    "ukmo_global_deterministic_10km": SP, "cma_grapes_global": UV,
    "meteofrance_arpege_world025": UV, "cmc_gem_gdps_15km": SP,
}
STATIC_FALLBACK = {"ncep_aigfs025": "ncep_gfs025"}

_local = threading.local()
def fs():
    if not hasattr(_local, "fs"):
        _local.fs = s3fs.S3FileSystem(anon=True, default_block_size=64 * 1024, skip_instance_cache=True, config_kwargs={"max_pool_connections": 8})
    return _local.fs

def read_cell(r, idx):
    y, x = idx
    return np.asarray(r[y:y + 1, x:x + 1, :]).ravel() if len(r.shape) == 3 else np.asarray(r[y:y+1, x:x+1]).ravel()

def select_cells(model, stations):
    """Per station choose, among the 2x2 enclosing cells, the land cell whose elevation best matches."""
    cache = os.path.join(OUT, f"cells_{model}.json")
    if os.path.exists(cache):
        return json.load(open(cache))
    sdom = STATIC_FALLBACK.get(model, model)
    h = omfiles.OmFileReader.from_fsspec(fs(), f"openmeteo/data/{sdom}/static/HSURF.om")
    # find a run file to derive grid
    probe = f"openmeteo/data_run/{model}/2026/09/15/"
    run = sorted(fs().ls(probe))[0]
    r = omfiles.OmFileReader.from_fsspec(fs(), run + "/temperature_2m.om")
    g = grid_from_reader(r)
    if sdom != model:
        hg = grid_from_reader(omfiles.OmFileReader.from_fsspec(fs(), sorted(fs().ls(f"openmeteo/data_run/ncep_gfs025/2026/09/15/"))[0] + "/wind_gusts_10m.om"))
    out = []
    for s in stations.itertuples():
        best = None
        for idx, clat, clon in g.candidates(s.lat, s.lon):
            if sdom != model:
                # same 0.25 grid for aigfs and gfs025
                z = float(read_cell(h, idx)[0])
            else:
                z = float(read_cell(h, idx)[0])
            if z <= -998:  # sea
                continue
            score = abs(z - s.elev)
            if best is None or score < best[0]:
                best = (score, list(idx), z, clat, clon)
        if best is None:  # all sea: nearest
            idx, clat, clon = g.candidates(s.lat, s.lon)[0]
            best = (None, list(idx), 0.0, clat, clon)
        out.append(dict(sid=s.sid, idx=best[1], z=best[2], lat=float(best[3]), lon=float(best[4])))
    json.dump(out, open(cache, "w"))
    return out

def extract_run(model, run_path, cells):
    tag = run_path.split("data_run/")[1].replace("/", "_")
    dest = os.path.join(OUT, f"{tag}.npz")
    if os.path.exists(dest):
        return dest, "cached"
    arrs = {}
    for var in MODELS[model]:
        try:
            r = omfiles.OmFileReader.from_fsspec(fs(), f"{run_path}/{var}.om")
        except Exception as e:
            return dest, f"missing {var}: {e}"
        t = np.asarray(r.get_child_by_name("time").read_array((slice(None),)))
        vals = np.stack([read_cell(r, tuple(c["idx"])) for c in cells])
        arrs[f"{var}__time"] = t
        arrs[var] = vals.astype(np.float32)
    np.savez_compressed(dest, **arrs)
    return dest, "ok"

def run_dates(start, end, hour="0000Z"):
    d = start
    while d <= end:
        yield d.strftime("%Y/%m/%d/") + hour
        d += dt.timedelta(days=1)

if __name__ == "__main__":
    models = sys.argv[1].split(",") if len(sys.argv) > 1 and sys.argv[1] != "all" else list(MODELS)
    hours = sys.argv[2].split(",") if len(sys.argv) > 2 else ["0000Z"]
    start = dt.date(2026, 8, 10); end = dt.date(2026, 11, 6)
    stations = load_station_meta()
    tasks = []
    with ThreadPoolExecutor(max_workers=len(models)) as ex:
        cell_map = dict(zip(models, ex.map(lambda m: select_cells(m, stations), models)))
    print("cells ready", flush=True)
    for m in models:
        cells = cell_map[m]
        for h in hours:
            for rd in run_dates(start, end, h):
                tasks.append((m, f"openmeteo/data_run/{m}/{rd}", cells))
    print("tasks", len(tasks), flush=True)
    t0 = time.time(); done = 0; errs = []
    with ThreadPoolExecutor(max_workers=int(os.environ.get("WORKERS", "40"))) as ex:
        futs = {ex.submit(extract_run, *t): t for t in tasks}
        for f in as_completed(futs):
            done += 1
            try:
                dest, st = f.result()
                if st not in ("ok", "cached"):
                    errs.append((futs[f][1], st))
            except Exception as e:
                errs.append((futs[f][1], repr(e)))
            if done % 50 == 0:
                print(f"{done}/{len(tasks)} {time.time()-t0:.0f}s errs={len(errs)}", flush=True)
    print("done", time.time() - t0, "errors", len(errs))
    for e in errs[:30]: print("ERR", e)
    json.dump(errs, open(os.path.join(OUT, "errors.json"), "w"))
