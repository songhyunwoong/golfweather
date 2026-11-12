"""Per-run dumps of pipeline/verify.py (--dump DIR) -> pairs_runs.npz for experiment.py.
Samples are (run, slot of the lead day): T, W [model, lead day 1..15, station, run*4+slot]; P [.., run*2+slot] (12 h totals).
Observations and valid times depend on the lead: OT, OW, OP [lead, station, sample]; tv6, tv12 [lead, sample] in days since 1970."""
import glob, os, sys, datetime as dt, numpy as np
S = os.path.dirname(os.path.abspath(__file__)); src = sys.argv[1]
MODELS = ["icon", "ifs", "aifs", "gfs", "ukmo", "blend"]; LEADS = 15
files = sorted(glob.glob(os.path.join(src, "*.npz"))); R = len(files); ns = np.load(files[0])["obs_t"].shape[0]
T = np.full((len(MODELS), LEADS, ns, R * 4), np.nan, "float32"); W = T.copy(); P = np.full((len(MODELS), LEADS, ns, R * 2), np.nan, "float32")
OT = np.full((LEADS, ns, R * 4), np.nan, "float32"); OW = OT.copy(); OP = np.full((LEADS, ns, R * 2), np.nan, "float32")
tv6 = np.full((LEADS, R * 4), np.nan); tv12 = np.full((LEADS, R * 2), np.nan)
for r, f in enumerate(files):
    z = np.load(f); b = os.path.basename(f); init = dt.datetime(int(b[:4]), int(b[4:6]), int(b[6:8]), int(b[8:10]), tzinfo=dt.timezone.utc).timestamp() / 86400
    for L in range(LEADS):                                   # dump columns: hours 6, 12, ... (6-hourly) and 12, 24, ... (12 h totals)
        c6 = slice(L * 4, L * 4 + 4); c12 = slice(L * 2, L * 2 + 2); s6 = slice(r * 4, r * 4 + 4); s12 = slice(r * 2, r * 2 + 2)
        OT[L, :, s6] = z["obs_t"][:, c6]; OW[L, :, s6] = z["obs_w"][:, c6]; OP[L, :, s12] = z["obs_p"][:, c12]
        tv6[L, s6] = init + (L * 24 + np.array([6, 12, 18, 24])) / 24; tv12[L, s12] = init + (L * 24 + np.array([12, 24])) / 24
        for m, k in enumerate(MODELS):
            if f"{k}_t" in z.files: T[m, L, :, s6] = z[f"{k}_t"][:, c6]; W[m, L, :, s6] = z[f"{k}_w"][:, c6]; P[m, L, :, s12] = z[f"{k}_p"][:, c12]
keep = np.isfinite(OT).any(axis=(0, 2))                      # stations that report
np.savez_compressed(os.path.join(S, "pairs_runs.npz"), T=T[:, :, keep], W=W[:, :, keep], P=P[:, :, keep], OT=OT[:, keep], OW=OW[:, keep], OP=OP[:, keep],
                    tv6=tv6, tv12=tv12, models=np.array(MODELS), leads=np.arange(1, LEADS + 1))
print("runs", R, "stations", int(keep.sum()), "T valid", [round(float(np.isfinite(T[m, 0][keep]).mean()), 2) for m in range(len(MODELS))], "obs P valid", round(float(np.isfinite(OP[0, keep]).mean()), 2))
