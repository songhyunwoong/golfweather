"""Blend rule for temperature and wind, shared by build_forecasts.py and verify.py.

Each model is first shifted by its mean error (bias) for that lead day, then the models are averaged with
weights 1 / MAE^2. Both come from pipeline/blend_weights.json, which make_reliability.py rewrites daily from the
last 90 days of national verification. A model or lead day without an entry gets weight 1 and no shift, so
without the file this is a plain mean. See research/blend-weights/ for the experiment behind it."""
import json, os
MEMBERS = ["icon", "aifs", "ifs", "ukmo", "gfs"]          # temperature and wind
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blend_weights.json")

def load(path=PATH):
    try: return json.load(open(path))["leads"]
    except (OSError, ValueError, KeyError): return {}

def coef(table, var, lead_day, model):
    """(weight, bias) of a model for var 't' or 'w' at a lead day (1..15)."""
    e = table.get(str(int(lead_day)), {}).get(var, {}).get(model)
    return (e["w"], e["b"]) if e else (1.0, 0.0)

def make(nat, min_n=500):
    """Table from the national block of reliability.json: {lead: {var: {model: {w, b}}}}; weights sum to 1 per lead and variable."""
    out = {}
    for lead in map(str, range(1, 16)):
        for var in ("t", "w"):
            raw = {m: nat[m][lead] for m in MEMBERS if lead in nat.get(m, {}) and nat[m][lead]["n_t"] >= min_n and nat[m][lead][var + "_mae"] > 0}
            if len(raw) < 2: continue
            inv = {m: 1 / r[var + "_mae"] ** 2 for m, r in raw.items()}; s = sum(inv.values())
            out.setdefault(lead, {})[var] = {m: dict(w=round(inv[m] / s, 3), b=raw[m][var + "_bias"]) for m in raw}
    return out
