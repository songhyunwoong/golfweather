"""Load GHCNh hourly observations (KMA ASOS/airport stations) -> hourly t2m/wind and 6-hourly precipitation.

Precipitation: GHCNh's generic `precipitation` column mixes accumulation periods for Korean BUFR reports,
so we use the BUFR (FM94) `precipitation_3_hour` group, which is consistent with the BUFR 24 h totals
(97-100 % agreement). A missing 3 h group in an hour that has a BUFR report means 0 mm.
"""
import pandas as pd, numpy as np, os
OBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "obs")
RT = ["temperature_Report_Type", "wind_speed_Report_Type", "dew_point_temperature_Report_Type", "sea_level_pressure_Report_Type"]
COLS = ["DATE", "temperature", "temperature_Quality_Code", "wind_speed", "wind_speed_Quality_Code",
        "precipitation_3_hour", "precipitation_3_hour_Report_Type"] + RT
BAD_QC = {2.0, 3.0, 6.0, 7.0}  # ISD-style suspect/erroneous flags

def load_station(sid):
    d = pd.read_csv(os.path.join(OBS_DIR, f"{sid}.psv"), sep="|", usecols=COLS, low_memory=False)
    d["time"] = pd.to_datetime(d.DATE, utc=True)
    d = d[d.time.dt.minute == 0].drop_duplicates("time", keep="last").set_index("time").sort_index()
    out = pd.DataFrame(index=d.index)
    for name, col, qc, lo, hi in [("t2m", "temperature", "temperature_Quality_Code", -40, 45),
                                  ("wind", "wind_speed", "wind_speed_Quality_Code", 0, 60)]:
        v = d[col].astype(float)
        v = v.mask(pd.to_numeric(d[qc], errors="coerce").isin(BAD_QC))
        out[name] = v.where((v >= lo) & (v <= hi))
    if not sid.startswith("KSI"):  # airports (METAR) carry no precipitation
        has_bufr = np.zeros(len(d), bool)
        for c in RT:
            has_bufr |= d[c].eq("FM94_1").values
        r3 = d.precipitation_3_hour.where(d.precipitation_3_hour_Report_Type.eq("FM94_1")).astype(float)
        out["p3"] = r3.where(r3.notna(), np.where(has_bufr, 0.0, np.nan))
    full = pd.date_range(out.index.min().floor("D"), out.index.max(), freq="h", tz="UTC")
    out = out.reindex(full)
    if "p3" in out:
        # BUFR 3 h groups are reliable at hours 02,05,...,23 UTC (often dropped at main synoptic hours).
        # 12 h total ending 11/23 UTC (= model window ending 12/00 UTC, 1 h offset), 24 h ending 23 UTC.
        p = out.p3
        out["p12"] = p + p.shift(3) + p.shift(6) + p.shift(9)
        out["p24"] = out.p12 + out.p12.shift(12)
    return out
