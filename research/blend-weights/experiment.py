"""Blend experiment: does weighting models by lead day and by variable beat the current simple blend?

Rolling evaluation, like operations: for every test month the weights / bias corrections are learned from the
90 days before that month and applied to the month itself. Scores are pooled over all test months.
Input: pairs_main.npz (pairs_prev.py).  Output: results/*.csv and a printed summary."""
import os, sys, csv, datetime as dt, numpy as np
S = os.path.dirname(os.path.abspath(__file__))
Z = np.load(sys.argv[1] if len(sys.argv) > 1 else os.path.join(S, "pairs_main.npz"))
TRAIN_DAYS = int(os.environ.get("TRAIN_DAYS", 90))
models = [str(m) for m in Z["models"]]; leads = [int(x) for x in Z["leads"]]
SHORT = {"ecmwf_aifs025_single": "aifs", "ecmwf_ifs": "ifs", "gfs_global": "gfs", "icon_global": "icon", "ukmo_global_deterministic_10km": "ukmo"}
names = [SHORT.get(m, m) for m in models]
CUR = [names.index(x) for x in ("icon", "aifs", "ifs", "ukmo")]            # members of the current blend
ALL = list(range(len(names)))
t6 = [dt.datetime.fromisoformat(str(x)) for x in Z["t6"]]; t12 = [dt.datetime.fromisoformat(str(x)) for x in Z["t12"]]
SEASON = {12: "겨울", 1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄", 6: "여름", 7: "여름", 8: "여름", 9: "가을", 10: "가을", 11: "가을"}

def months(tt):
    ms = sorted({(t.year, t.month) for t in tt}); out = []
    for y, m in ms:
        start = dt.datetime(y, m, 1, tzinfo=tt[0].tzinfo)
        if (start - tt[0]).days < TRAIN_DAYS: continue
        test = np.array([(t.year, t.month) == (y, m) for t in tt]); train = np.array([start - dt.timedelta(days=TRAIN_DAYS) <= t < start for t in tt])
        out.append(((y, m), train, test))
    return out

def wmean(F, w, idx):
    """Weighted mean over models idx (weights w[idx]); members that are missing drop out and weights renormalise. Needs >= 2 members."""
    X = F[idx]; ok = np.isfinite(X); ww = np.asarray(w, float)[idx].reshape(-1, *[1] * (X.ndim - 1)) * ok
    s = ww.sum(0); out = np.where(ok, X, 0) * ww
    return np.where((ok.sum(0) >= 2) & (s > 0), out.sum(0) / np.where(s > 0, s, 1), np.nan)

# ---------- temperature and wind ----------
def cont(var, OV):
    F = Z[var].astype(float); O = Z[OV].astype(float)                      # F [m, lead, s, t], O [s, t]
    hours = np.array([t.hour for t in t6]); acc = {}; seas = {}
    for (y, mo), tr, te in months(t6):
        for li, L in enumerate(leads):
            f = F[:, li]; e = f - O[None]
            bias = np.zeros((len(names), 1, len(t6)))                          # per model and hour of day, all stations pooled
            for h in (0, 6, 12, 18):
                sel = tr & (hours == h)
                with np.errstate(all="ignore"): b = np.nanmean(e[:, :, sel], axis=(1, 2))
                bias[:, 0, hours == h] = np.where(np.isfinite(b), b, 0)[:, None]
            fc = f - bias
            with np.errstate(all="ignore"):
                mse = np.nanmean(e[:, :, tr] ** 2, axis=(1, 2)); msec = np.nanmean((fc - O[None])[:, :, tr] ** 2, axis=(1, 2))
            inv = lambda v, k: np.where(np.isfinite(v) & (v > 0), 1 / np.where(v > 0, v, 1) ** k, 0)
            one = np.ones(len(names))
            P = {"현재(4모델 평균)": wmean(f, one, CUR), "5모델 평균": wmean(f, one, ALL),
                 "성적 가중(5)": wmean(f, inv(mse, 1), ALL), "성적 가중 강하게(5)": wmean(f, inv(mse, 2), ALL),
                 "치우침 보정+평균(4)": wmean(fc, one, CUR), "치우침 보정+평균(5)": wmean(fc, one, ALL),
                 "치우침 보정+성적 가중(5)": wmean(fc, inv(msec, 1), ALL), "치우침 보정+성적 가중 강하게(5)": wmean(fc, inv(msec, 2), ALL)}
            for i, n in enumerate(names): P["단일 " + n] = f[i]; P["단일 " + n + " 보정"] = fc[i]
            base = np.isfinite(P["현재(4모델 평균)"]) & np.isfinite(O) & te[None]
            for k, p in P.items():
                ok = base & np.isfinite(p); a = np.abs(p - O)[ok]
                for d, key in ((acc, (k, L)), (seas, (k, L, SEASON[mo]))):
                    s = d.setdefault(key, [0.0, 0, 0.0]); s[0] += a.sum(); s[1] += a.size; s[2] += (p - O)[ok].sum()
    return acc, seas

# ---------- precipitation (12 h totals, rain = 1 mm or more) ----------
def ets(h, f, m, c):
    n = h + f + m + c
    if n == 0 or h + m == 0: return np.nan, np.nan
    r = (h + f) * (h + m) / n; d = h + f + m - r
    return ((h - r) / d if d else np.nan), (h + f) / (h + m)
def table(pred, obs, ok):
    p, o = pred[ok], obs[ok]; return np.array([(p & o).sum(), (p & ~o).sum(), (~p & o).sum(), (~p & ~o).sum()], float)
def wmed(X):
    with np.errstate(all="ignore"): m = np.nanmedian(X, axis=0)
    return np.where(np.isfinite(X).sum(0) >= 2, m, np.nan)

def rain():
    F = Z["P"].astype(float); O = Z["OP"].astype(float); acc = {}; seas = {}; chosen = {}
    THR = [0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]
    for (y, mo), tr, te in months(t12):
        for li, L in enumerate(leads):
            f = F[:, li]; ob = O >= 1.0; fin = np.isfinite(O)
            sk = np.zeros(len(names))                                           # each model's ETS on the training window
            for i in range(len(names)):
                e, _ = ets(*table(f[i] >= 1.0, ob, fin & np.isfinite(f[i]) & tr[None])); sk[i] = max(e, 0.02) if np.isfinite(e) else 0
            one = np.ones(len(names)); yes = np.where(np.isfinite(f), (f >= 1.0).astype(float), np.nan)
            med4 = wmed(f[CUR]); med5 = wmed(f); mean4 = wmean(f, one, CUR); mean5 = wmean(f, one, ALL); wm5 = wmean(f, sk, ALL); wm5b = wmean(f, sk ** 2, ALL)
            vote = wmean(yes, one, ALL); wvote = wmean(yes, sk, ALL)
            def tuned(x, tag):                                                  # threshold with the best training ETS
                best = max(THR, key=lambda th: (lambda r: -9 if not np.isfinite(r) else r)(ets(*table(x >= th, ob, fin & np.isfinite(x) & tr[None]))[0]))
                chosen.setdefault((tag, L), []).append(best); return x >= best
            P = {"현재(4모델 중앙값 ≥1mm)": (med4 >= 1.0, med4), "5모델 중앙값": (med5 >= 1.0, med5), "4모델 평균": (mean4 >= 1.0, mean4), "5모델 평균": (mean5 >= 1.0, mean5),
                 "성적 가중 평균(5)": (wm5 >= 1.0, wm5), "성적 가중 평균(5)+기준 조정": (tuned(wm5, "wm5"), wm5), "5모델 평균+기준 조정": (tuned(mean5, "mean5"), mean5),
                 "성적 가중 강하게(5)+기준 조정": (tuned(wm5b, "wm5b"), wm5b),
                 "다수결(5 중 과반)": (vote > 0.5, vote), "다수결(5 중 2 이상)": (vote >= 0.4, vote), "성적 가중 다수결": (wvote >= 0.5, wvote), "성적 가중 다수결(40%)": (wvote >= 0.4, wvote)}
            for i, n in enumerate(names): P["단일 " + n] = (f[i] >= 1.0, f[i])
            base = np.isfinite(med4) & fin & te[None]
            for k, (b, x) in P.items():
                ok = base & np.isfinite(x)
                for d, key in ((acc, (k, L)), (seas, (k, L, SEASON[mo]))): d[key] = d.get(key, 0) + table(b, ob, ok)
    return acc, seas, chosen

def show(title, rows, fmt):
    print("\n" + title); print(f"{'방식':34s}" + "".join(f"{'D+' + str(L):>8s}" for L in leads))
    for k, v in rows: print(f"{k:34s}" + "".join(f"{fmt(x):>8s}" for x in v))
os.makedirs(os.path.join(S, "results"), exist_ok=True)
def save(name, header, rows):
    with open(os.path.join(S, "results", name), "w", newline="") as f: w = csv.writer(f); w.writerow(header); w.writerows(rows)

print("test months:", [m for m, _, _ in months(t6)], "train days:", TRAIN_DAYS)
for var, OV, label in (("T", "OT", "기온 평균 오차(°C)"), ("W", "OW", "풍속 평균 오차(m/s)")):
    acc, seas = cont(var, OV); keys = list(dict.fromkeys(k for k, _ in acc))
    mae = lambda k, L, d=acc, s=None: (lambda v: v[0] / v[1] if v and v[1] else np.nan)(d.get((k, L) if s is None else (k, L, s)))
    show(label, [(k, [mae(k, L) for L in leads]) for k in keys], lambda x: f"{x:.3f}")
    save(f"{var}_mae.csv", ["method", "api_lead", "mae", "bias", "n"], [[k, L, round(mae(k, L), 4), round(acc[(k, L)][2] / max(acc[(k, L)][1], 1), 4), acc[(k, L)][1]] for k in keys for L in leads])
    save(f"{var}_mae_season.csv", ["method", "api_lead", "season", "mae", "n"], [[k, L, s, round(v[0] / v[1], 4), v[1]] for (k, L, s), v in sorted(seas.items()) if v[1]])
acc, seas, chosen = rain(); keys = list(dict.fromkeys(k for k, _ in acc))
show("비 유무 적중(ETS, 12시간 1mm 이상)", [(k, [ets(*acc[(k, L)])[0] for L in leads]) for k in keys], lambda x: f"{x:.3f}")
show("비 예보 빈도(FBI, 1이 적정)", [(k, [ets(*acc[(k, L)])[1] for L in leads]) for k in keys], lambda x: f"{x:.2f}")
save("rain_ets.csv", ["method", "api_lead", "ets", "fbi", "hits", "false_alarms", "misses", "correct_neg"], [[k, L, *[round(x, 4) for x in ets(*acc[(k, L)])], *acc[(k, L)].astype(int)] for k in keys for L in leads])
save("rain_ets_season.csv", ["method", "api_lead", "season", "ets", "fbi", "wet"], [[k, L, s, *[round(x, 4) for x in ets(*v)], int(v[0] + v[2])] for (k, L, s), v in sorted(seas.items())])
print("\n조정된 기준(mm) 중앙값:", {k: float(np.median(v)) for k, v in sorted(chosen.items())})
