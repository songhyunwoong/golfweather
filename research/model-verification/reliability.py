"""App-facing reliability score (0-100) per model x lead day, from the verification tables.
   rain  : ETS(12 h, >=1 mm)   0.0 -> 0 pts, 0.5 -> 100 pts
   temp  : MAE(2 m)            3.0 C -> 0 pts, 1.0 C -> 100 pts
   wind  : MAE(10 m)           2.0 m/s -> 0 pts, 0.8 m/s -> 100 pts
   score = 0.45*rain + 0.35*temp + 0.20*wind ; stars: >=85 5, >=70 4, >=55 3, >=40 2, else 1"""
import pandas as pd, numpy as np, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(HERE, "results"); sys.path.insert(0, HERE)
from score import NAMES
lin = lambda x, a, b: np.clip(100 * (x - a) / (b - a), 0, 100)
t = pd.read_csv(os.path.join(R, "t2m_by_day.csv"))[["model", "day", "MAE"]].rename(columns={"MAE": "t"})
w = pd.read_csv(os.path.join(R, "wind_by_day.csv"))[["model", "day", "MAE"]].rename(columns={"MAE": "w"})
p = pd.read_csv(os.path.join(R, "precip_by_day.csv")); p = p[(p.win == 12) & (p.thr == 1.0)][["model", "day", "ETS"]]
d = t.merge(w, on=["model", "day"]).merge(p, on=["model", "day"])
d["rain_pts"] = lin(d.ETS, 0.0, 0.5); d["temp_pts"] = lin(d.t, 3.0, 1.0); d["wind_pts"] = lin(d.w, 2.0, 0.8)
d["score"] = 0.45 * d.rain_pts + 0.35 * d.temp_pts + 0.20 * d.wind_pts
d["stars"] = pd.cut(d.score, [-1, 40, 55, 70, 85, 101], labels=[1, 2, 3, 4, 5]).astype(int)
d.round(3).to_csv(os.path.join(R, "reliability_score.csv"), index=False)
x = d.pivot(index="model", columns="day", values="score").round(0)
print(x[[c for c in [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15] if c in x.columns]].rename(index=NAMES).sort_values(1, ascending=False).to_string())
