"""Charts of verification results (PNG for the markdown plan)."""
import pandas as pd, numpy as np, os, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import koreanize_matplotlib  # noqa: F401  (NanumGothic)
HERE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(HERE, "results")
sys.path.insert(0, HERE)
from score import NAMES
SURF, INK, INK2, MUTED, GRID, AXIS, OTHER = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#c9c8c1"
HL = [("dwd_icon", "#2a78d6"), ("ecmwf_ifs", "#eb6834"), ("BLEND4", "#1baf7a")]
SHORT = {"dwd_icon": "ICON", "ecmwf_ifs": "ECMWF IFS 9km", "BLEND4": "블렌드(4모델 평균)", "ecmwf_aifs025_single": "ECMWF AIFS (AI)"}

def panel(ax, df, val, title, ylabel, best_low=True, extra=None):
    hl = dict(HL + ([extra] if extra else []))
    for m, g in df.groupby("model"):
        if m in hl: continue
        g = g.sort_values("day"); ax.plot(g.day, g[val], color=OTHER, lw=1.0, zorder=1)
    labels = []
    for m, c in hl.items():
        g = df[df.model == m].sort_values("day")
        if g.empty: continue
        ax.plot(g.day, g[val], color=c, lw=2.0, zorder=3, marker="o", ms=4.5, markeredgecolor=SURF, markeredgewidth=1.2)
        labels.append([g.day.iloc[-1], g[val].iloc[-1], SHORT.get(m, NAMES.get(m, m))])
    # simple label repel: labels ending on the same day keep a minimum vertical gap
    lo_, hi_ = df[val].min(), df[val].max(); gap = 0.055 * (hi_ - lo_)
    for x in set(l[0] for l in labels):
        grp = sorted([l for l in labels if l[0] == x], key=lambda l: l[1])
        for i in range(1, len(grp)):
            if grp[i][1] - grp[i - 1][1] < gap: grp[i][1] = grp[i - 1][1] + gap
    for x, y, lab in labels:
        ax.annotate(lab, (x, y), xytext=(6, 0), textcoords="offset points", va="center", fontsize=8.5, color=INK2,
                    bbox=dict(boxstyle="round,pad=0.15", fc=SURF, ec="none", alpha=0.85))
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=8)
    ax.set_ylabel(ylabel, color=INK2, fontsize=9); ax.set_xlabel("예보 리드타임 (일)", color=INK2, fontsize=9)
    ax.set_facecolor(SURF); ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
    for sp in ["top", "right"]: ax.spines[sp].set_visible(False)
    for sp in ["left", "bottom"]: ax.spines[sp].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=8.5); ax.set_xticks(range(1, 16))
    ax.set_xlim(0.6, 16.4)

def main(extra_model=None, extra_color="#eda100"):
    t = pd.read_csv(os.path.join(R, "t2m_by_day.csv")); w = pd.read_csv(os.path.join(R, "wind_by_day.csv"))
    p = pd.read_csv(os.path.join(R, "precip_by_day.csv"))
    extra = (extra_model, extra_color) if extra_model else None
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.6), facecolor=SURF)
    panel(axs[0], t, "MAE", "기온 2 m 평균절대오차 (낮을수록 좋음)", "°C", extra=extra)
    panel(axs[1], w, "MAE", "풍속 10 m 평균절대오차 (낮을수록 좋음)", "m/s", extra=extra)
    pp = p[(p.win == 12) & (p.thr == 1.0)]
    panel(axs[2], pp, "ETS", "12시간 강수 ≥1 mm 적중 ETS (높을수록 좋음)", "ETS", best_low=False, extra=extra)
    handles = [plt.Line2D([], [], color=c, lw=2, marker="o", ms=4.5) for _, c in HL + ([extra] if extra else [])]
    labels = [SHORT.get(m, NAMES.get(m, m)) for m, _ in HL + ([extra] if extra else [])]
    handles.append(plt.Line2D([], [], color=OTHER, lw=1)); labels.append("기타 모델 (GFS, UKMO, JMA, CMA, GEM, ARPEGE, AI 모델 등)")
    fig.legend(handles, labels, loc="upper center", ncol=len(labels), frameon=False, fontsize=9, bbox_to_anchor=(0.5, 1.02), labelcolor=INK2)
    fig.text(0.01, -0.03, "자료: Open-Meteo 오픈데이터(실행별 예보) vs 기상청 ASOS·공항 32개 지점 관측(NOAA GHCNh). 2026-08-10~11-06 00/12 UTC 실행, 동일 표본 비교.", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = os.path.join(R, "skill_by_lead.png"); fig.savefig(out, dpi=200, bbox_inches="tight", facecolor=SURF); print(out)

if __name__ == "__main__":
    main(*(sys.argv[1:2]))
