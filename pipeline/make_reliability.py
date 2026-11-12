"""site/reliability.json: rolling reliability scores by region, model and lead day.

Adds up data/verification/daily/*.json (written by verify.py) over the last WINDOW init dates.
score = 0.45 * rain + 0.35 * temperature + 0.20 * wind, each part 0..100:
  rain  ETS of 12 h precipitation >= 1 mm   0 -> 0, 0.5 -> 100
  temp  MAE of 2 m temperature              3.0 C -> 0, 1.0 C -> 100
  wind  MAE of 10 m wind speed              2.0 m/s -> 0, 0.8 m/s -> 100
"""
import glob, json, os, time
import blendrule
HERE = os.path.dirname(os.path.abspath(__file__))
VDIR = os.path.join(HERE, "..", "data", "verification")
WINDOW = 90
lin = lambda x, a, b: max(0.0, min(100.0, 100 * (x - a) / (b - a)))

def main():
    files = sorted(glob.glob(os.path.join(VDIR, "daily", "*.json")))[-WINDOW:]
    if not files: raise SystemExit("no verification files")
    tot = {}
    for f in files:
        for model, regs in json.load(open(f))["models"].items():
            for reg, leads in regs.items():
                for lead, c in leads.items():
                    t = tot.setdefault(reg, {}).setdefault(model, {}).setdefault(lead, dict.fromkeys(c, 0))
                    for k, v in c.items(): t[k] += v
    out = {}
    for reg, models in tot.items():
        for model, leads in models.items():
            for lead, c in leads.items():
                if c["t_n"] < 30 or c["w_n"] < 30: continue
                a, b, cc, d = c["a"], c["b"], c["c"], c["d"]; n = a + b + cc + d
                if n < 30: continue
                ar = (a + b) * (a + cc) / n; ets = (a - ar) / (a + b + cc - ar) if (a + b + cc - ar) > 0 else 0.0
                t_mae, w_mae = c["t_ae"] / c["t_n"], c["w_ae"] / c["w_n"]
                score = 0.45 * lin(ets, 0, 0.5) + 0.35 * lin(t_mae, 3.0, 1.0) + 0.20 * lin(w_mae, 2.0, 0.8)
                out.setdefault(reg, {}).setdefault(model, {})[lead] = dict(
                    score=round(score), stars=5 if score >= 85 else 4 if score >= 70 else 3 if score >= 55 else 2 if score >= 40 else 1,
                    t_mae=round(t_mae, 2), t_bias=round(c["t_e"] / c["t_n"], 2), w_mae=round(w_mae, 2), w_bias=round(c["w_e"] / c["w_n"], 2),
                    ets=round(ets, 2), fbi=round((a + b) / (a + cc), 2) if a + cc else None, n_t=c["t_n"], wet=a + cc, n_p=n)
    first, last = os.path.basename(files[0])[:-5], os.path.basename(files[-1])[:-5]
    stations = len(json.load(open(os.path.join(VDIR, "stations.json"))))
    json.dump(dict(generated=time.strftime("%Y-%m-%d"), period=f"{first} ~ {last}", init_dates=len(files), stations=stations,
                   note=f"기상청 지상관측 {stations}개 지점, 00·12 UTC 실행", regions=out),
              open(os.path.join(HERE, "..", "site", "reliability.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    nat = out.get("전국", {})
    json.dump(dict(generated=time.strftime("%Y-%m-%d"), period=f"{first} ~ {last}", note="weight 1/MAE^2 and bias per model, lead day and variable (t: C, w: m/s)",
                   leads=blendrule.make(nat)), open(blendrule.PATH, "w"), separators=(",", ":"))
    print(f"reliability: {first}..{last} ({len(files)} init dates)")
    for m in nat: print(f"  {m:6s}", " ".join(f"{nat[m].get(str(d), {}).get('score', '--'):>3}" for d in range(1, 16)))

if __name__ == "__main__":
    main()
