import pandas as pd, glob, os
OBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "obs")
def load_station_meta():
    rows = []
    for f in sorted(glob.glob(os.path.join(OBS_DIR, "*.psv"))):
        sid = os.path.basename(f)[:-4]
        d = pd.read_csv(f, sep="|", usecols=["Station_name","LATITUDE","LONGITUDE","ELEVATION"], nrows=5)
        r = d.iloc[0]
        rows.append(dict(sid=sid, name=r.Station_name.strip(), lat=float(r.LATITUDE), lon=float(r.LONGITUDE), elev=float(r.ELEVATION)))
    return pd.DataFrame(rows)
if __name__ == "__main__":
    print(load_station_meta().to_string())
