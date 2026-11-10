"""Diagnose whether stored precipitation for multi-hour steps is an interval sum or an hourly mean rate."""
import numpy as np, glob, os, collections, sys
sys.path.insert(0, os.path.dirname(__file__))
from obs import load_station
from stations import load_station_meta
res = collections.defaultdict(lambda: collections.defaultdict(list))
for f in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw", "*.npz")):
    m = os.path.basename(f).split("_2026_")[0]
    z = np.load(f)
    t = z["precipitation__time"]; v = z["precipitation"]
    dt = np.diff(t) / 3600
    for k, d in enumerate(dt):
        res[m][int(d)].append(np.nanmean(v[:, k + 1]))
for m in sorted(res):
    print(f"{m:32s}", {d: (round(float(np.mean(x)), 3), len(x)) for d, x in sorted(res[m].items())})
# observed mean hourly rate
st = load_station_meta(); tot = []; hrs = []
for s in st.sid:
    if s.startswith("KSI"): continue
    o = load_station(s).loc["2026-08-10":"2026-11-07"]
    p = o.p24[o.index.hour == 23].dropna()
    tot.append(p.sum()); hrs.append(24 * len(p))
print("observed mean rate mm/h:", round(sum(tot) / sum(hrs), 3))
