"""Blend experiment: does weighting models by lead day and by variable beat the current simple blend?

Rolling evaluation, like operations: for every test block (TEST_DAYS of valid times) the weights and bias
corrections are learned from the TRAIN_DAYS before the block and applied to the block. Scores are pooled over all blocks.
Input: pairs_runs.npz (pairs_dump.py) or pairs_main.npz (pairs_prev.py).  Output: results/*.csv and a printed summary."""
import os, sys, csv, datetime as dt, numpy as np
S = os.path.dirname(os.path.abspath(__file__))
Z = np.load(sys.argv[1] if len(sys.argv) > 1 else os.path.join(S, "pairs_runs.npz"))
TRAIN_DAYS = int(os.environ.get("TRAIN_DAYS", 30)); TEST_DAYS = int(os.environ.get("TEST_DAYS", 7))
OUT = os.path.join(S, os.environ.get("OUT", "results"))
SHORT = {"ecmwf_aifs025_single": "aifs", "ecmwf_ifs": "ifs", "gfs_global": "gfs", "icon_global": "icon", "ukmo_global_deterministic_10km": "ukmo"}
allnames = [SHORT.get(str(m), str(m)) for m in Z["models"]]; leads = [int(x) for x in Z["leads"]]
REF = allnames.index("blend") if "blend" in allnames else None          # the app's own blend, if stored: reference row only
MI = [i for i, n in enumerate(allnames) if n != "blend"]; names = [allnames[i] for i in MI]; NM = len(names)
CUR = [names.index(x) for x in ("icon", "aifs", "ifs", "ukmo")]            # members of the current blend
ALL = list(range(NM))
if "tv6" in Z.files:                                                       # per-run samples: observation and valid time depend on the lead
    D = {k: Z[k].astype(float) for k in ("T", "W", "P", "OT", "OW", "OP", "tv6", "tv12")}
else:                                                                      # valid-time samples (pairs_prev.py)
    nl = len(leads); rep = lambda o: np.repeat(o[None].astype(float), nl, 0)
    day = lambda arr: np.array([dt.datetime.fromisoformat(str(x)).timestamp() / 86400 for x in arr])
    D = dict(T=Z["T"].astype(float), W=Z["W"].astype(float), P=Z["P"].astype(float), OT=rep(Z["OT"]), OW=rep(Z["OW"]), OP=rep(Z["OP"]),
             tv6=np.repeat(day(Z["t6"])[None], nl, 0), tv12=np.repeat(day(Z["t12"])[None], nl, 0))

def blocks(tv):
    t0, t1 = np.nanmin(tv), np.nanmax(tv); out = []; s0 = np.floor(t0) + TRAIN_DAYS
    while s0 < t1: out.append(s0); s0 += TEST_DAYS
    return out
label = lambda s0: dt.datetime.fromtimestamp(s0 * 86400, dt.timezone.utc).strftime("%Y-%m")

def wmean(F, w, idx):
    """Weighted mean over models idx; missing members drop out and the weights renormalise. Needs >= 2 members."""
    X = F[idx]; ok = np.isfinite(X); ww = np.asarray(w, float)[idx].reshape(-1, *[1] * (X.ndim - 1)) * ok
    s = ww.sum(0); out = np.where(ok, X, 0) * ww
    return np.where((ok.sum(0) >= 2) & (s > 0), out.sum(0) / np.where(s > 0, s, 1), np.nan)
def wmed(X):
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings(): warnings.simplefilter("ignore"); m = np.nanmedian(X, axis=0)
    return np.where(np.isfinite(X).sum(0) >= 2, m, np.nan)
def nmean(x, axis):
    n = np.isfinite(x).sum(axis); return np.where(n > 0, np.nansum(x, axis) / np.maximum(n, 1), np.nan)

# ---------- temperature and wind ----------
def cont(var, OV):
    FA = D[var]; OA = D[OV]; acc = {}; per = {}; wlog = {}                    # FA [m, lead, s, n], OA [lead, s, n]
    for s0 in blocks(D["tv6"]):
        for li, L in enumerate(leads):
            tv = D["tv6"][li]; tr = (tv >= s0 - TRAIN_DAYS) & (tv < s0); te = (tv >= s0) & (tv < s0 + TEST_DAYS)
            if tr.sum() < 20 or not te.any(): continue
            hours = np.round((tv % 1) * 24).astype(int) % 24
            f = FA[MI][:, li]; O = OA[li]; e = f - O[None]
            bias = np.zeros((NM, 1, len(tv)))                                  # per model and hour of day, all stations pooled
            for h in (0, 6, 12, 18):
                b = nmean(e[:, :, tr & (hours == h)], (1, 2)); bias[:, 0, hours == h] = np.where(np.isfinite(b), b, 0)[:, None]
            fc = f - bias
            mse = nmean(e[:, :, tr] ** 2, (1, 2)); msec = nmean((fc - O[None])[:, :, tr] ** 2, (1, 2))
            inv = lambda v, k: np.where(np.isfinite(v) & (v > 0), 1 / np.where(v > 0, v, 1) ** k, 0)
            one = np.ones(NM); w2 = inv(msec, 2); wlog.setdefault(L, []).append(w2 / w2.sum() if w2.sum() else w2)
            P = {"현재(4모델 평균)": wmean(f, one, CUR), "5모델 평균": wmean(f, one, ALL),
                 "성적 가중(5)": wmean(f, inv(mse, 1), ALL), "성적 가중 강하게(5)": wmean(f, inv(mse, 2), ALL),
                 "치우침 보정+평균(4)": wmean(fc, one, CUR), "치우침 보정+평균(5)": wmean(fc, one, ALL),
                 "치우침 보정+성적 가중(5)": wmean(fc, inv(msec, 1), ALL), "치우침 보정+성적 가중 강하게(5)": wmean(fc, w2, ALL)}
            for i, n in enumerate(names): P["단일 " + n] = f[i]; P["단일 " + n + " 보정"] = fc[i]
            base = np.isfinite(P["현재(4모델 평균)"]) & np.isfinite(O) & te[None]
            for k, p in P.items():
                ok = base & np.isfinite(p); a = np.abs(p - O)[ok]
                for d, key in ((acc, (k, L)), (per, (k, L, label(s0)))):
                    s = d.setdefault(key, [0.0, 0, 0.0]); s[0] += a.sum(); s[1] += a.size; s[2] += (p - O)[ok].sum()
    return acc, per, wlog

# ---------- precipitation (12 h totals, rain = 1 mm or more) ----------
def ets(h, f, m, c):
    n = h + f + m + c
    if n == 0 or h + m == 0: return np.nan, np.nan
    r = (h + f) * (h + m) / n; d = h + f + m - r
    return ((h - r) / d if d else np.nan), (h + f) / (h + m)
def table(pred, obs, ok):
    p, o = pred[ok], obs[ok]; return np.array([(p & o).sum(), (p & ~o).sum(), (~p & o).sum(), (~p & ~o).sum()], float)

def rain():
    FA = D["P"]; OA = D["OP"]; acc = {}; per = {}; chosen = {}
    THR = [0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]
    for s0 in blocks(D["tv12"]):
        for li, L in enumerate(leads):
            tv = D["tv12"][li]; tr = (tv >= s0 - TRAIN_DAYS) & (tv < s0); te = (tv >= s0) & (tv < s0 + TEST_DAYS)
            if tr.sum() < 10 or not te.any(): continue
            f = FA[MI][:, li]; O = OA[li]; ob = O >= 1.0; fin = np.isfinite(O)
            sk = np.zeros(NM)                                                   # each model's ETS on the training window
            for i in range(NM):
                e, _ = ets(*table(f[i] >= 1.0, ob, fin & np.isfinite(f[i]) & tr[None])); sk[i] = max(e, 0.02) if np.isfinite(e) else 0
            one = np.ones(NM); yes = np.where(np.isfinite(f), (f >= 1.0).astype(float), np.nan)
            med4 = wmed(f[CUR]); med5 = wmed(f); mean4 = wmean(f, one, CUR); mean5 = wmean(f, one, ALL); wm5 = wmean(f, sk, ALL); wm5b = wmean(f, sk ** 2, ALL)
            vote = wmean(yes, one, ALL); wvote = wmean(yes, sk, ALL)
            def tuned(x, tag):                                                  # threshold with the best training ETS
                best = max(THR, key=lambda th: (lambda r: -9 if not np.isfinite(r) else r)(ets(*table(x >= th, ob, fin & np.isfinite(x) & tr[None]))[0]))
                chosen.setdefault((tag, L), []).append(best); return x >= best
            P = {"현재(4모델 중앙값 ≥1mm)": (med4 >= 1.0, med4), "5모델 중앙값": (med5 >= 1.0, med5), "4모델 평균": (mean4 >= 1.0, mean4), "5모델 평균": (mean5 >= 1.0, mean5),
                 "성적 가중 평균(5)": (wm5 >= 1.0, wm5), "성적 가중 평균(5)+기준 조정": (tuned(wm5, "wm5"), wm5), "5모델 평균+기준 조정": (tuned(mean5, "mean5"), mean5),
                 "4모델 중앙값+기준 조정": (tuned(med4, "med4"), med4), "성적 가중 강하게(5)+기준 조정": (tuned(wm5b, "wm5b"), wm5b),
                 "다수결(5 중 과반)": (vote > 0.5, vote), "다수결(5 중 2 이상)": (vote >= 0.4, vote), "성적 가중 다수결": (wvote >= 0.5, wvote), "성적 가중 다수결(40%)": (wvote >= 0.4, wvote)}
            if REF is not None: P["앱 블렌드(시간별 중앙값의 합)"] = (FA[REF, li] >= 1.0, FA[REF, li])
            for i, n in enumerate(names): P["단일 " + n] = (f[i] >= 1.0, f[i])
            base = np.isfinite(med4) & fin & te[None]
            for k, (b, x) in P.items():
                ok = base & np.isfinite(x)
                for d, key in ((acc, (k, L)), (per, (k, L, label(s0)))): d[key] = d.get(key, 0) + table(b, ob, ok)
    return acc, per, chosen

def show(title, rows, fmt):
    print("\n" + title); print(f"{'방식':34s}" + "".join(f"{'D' + str(L):>7s}" for L in leads))
    for k, v in rows: print(f"{k:34s}" + "".join(f"{fmt(x):>7s}" for x in v))
os.makedirs(OUT, exist_ok=True)
def save(name, header, rows):
    with open(os.path.join(OUT, name), "w", newline="") as f: w = csv.writer(f); w.writerow(header); w.writerows(rows)

print("test blocks:", len(blocks(D["tv6"])), "train days:", TRAIN_DAYS, "test days:", TEST_DAYS, "models:", names)
for var, OV, title in (("T", "OT", "기온 평균 오차(°C)"), ("W", "OW", "풍속 평균 오차(m/s)")):
    acc, per, wlog = cont(var, OV); keys = list(dict.fromkeys(k for k, _ in acc))
    mae = lambda k, L: (lambda v: v[0] / v[1] if v and v[1] else np.nan)(acc.get((k, L)))
    show(title, [(k, [mae(k, L) for L in leads]) for k in keys], lambda x: f"{x:.2f}")
    print("  표본 수:", [acc.get(("현재(4모델 평균)", L), [0, 0])[1] for L in leads])
    print("  평균 가중치(보정 후, 강하게) " + "/".join(names) + ":", {L: [round(float(x), 2) for x in np.mean(wlog[L], 0)] for L in leads if L in wlog})
    save(f"{var}_mae.csv", ["method", "lead_day", "mae", "bias", "n"], [[k, L, round(mae(k, L), 4), round(acc[(k, L)][2] / max(acc[(k, L)][1], 1), 4), acc[(k, L)][1]] for k in keys for L in leads if (k, L) in acc])
    save(f"{var}_mae_period.csv", ["method", "lead_day", "period", "mae", "n"], [[k, L, s, round(v[0] / v[1], 4), v[1]] for (k, L, s), v in sorted(per.items()) if v[1]])
acc, per, chosen = rain(); keys = list(dict.fromkeys(k for k, _ in acc)); E = lambda k, L: ets(*acc.get((k, L), np.zeros(4)))
show("비 유무 적중(ETS, 12시간 1mm 이상)", [(k, [E(k, L)[0] for L in leads]) for k in keys], lambda x: f"{x:.2f}")
show("비 예보 빈도(FBI, 1이 적정)", [(k, [E(k, L)[1] for L in leads]) for k in keys], lambda x: f"{x:.2f}")
print("  비 온 표본 수:", [int(acc.get(("현재(4모델 중앙값 ≥1mm)", L), np.zeros(4))[[0, 2]].sum()) for L in leads])
save("rain_ets.csv", ["method", "lead_day", "ets", "fbi", "hits", "false_alarms", "misses", "correct_neg"], [[k, L, *[round(x, 4) for x in E(k, L)], *acc[(k, L)].astype(int)] for k in keys for L in leads if (k, L) in acc])
save("rain_ets_period.csv", ["method", "lead_day", "period", "ets", "fbi", "wet"], [[k, L, s, *[round(x, 4) for x in ets(*v)], int(v[0] + v[2])] for (k, L, s), v in sorted(per.items())])
print("\n조정된 기준(mm) 중앙값:", {k: float(np.median(v)) for k, v in sorted(chosen.items())})
