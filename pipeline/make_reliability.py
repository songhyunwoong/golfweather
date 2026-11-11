"""site/reliability.json from the verification results (fixed values until phase 3 automates verification)."""
import csv, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
KEY = {"dwd_icon": "icon", "ecmwf_ifs": "ifs", "ecmwf_aifs025_single": "aifs", "ncep_gfs013": "gfs", "ukmo_global_deterministic_10km": "ukmo", "BLEND4": "blend"}
out = {}
for r in csv.DictReader(open(os.path.join(HERE, "..", "research", "model-verification", "results", "reliability_score.csv"))):
    k = KEY.get(r["model"]); d = int(r["day"])
    if k and d <= 8:
        out.setdefault(k, {})[d] = dict(score=round(float(r["score"])), stars=int(r["stars"]), t_mae=round(float(r["t"]), 2), w_mae=round(float(r["w"]), 2), ets=round(float(r["ETS"]), 2))
json.dump(dict(period="2026-08-10 ~ 2026-11-06", stations=32, note="전국 32개 지점, 00·12 UTC 실행 검증", models=out),
          open(os.path.join(HERE, "..", "site", "reliability.json"), "w"), ensure_ascii=False, separators=(",", ":"))
print({k: sorted(v) for k, v in out.items()})
