"""Golf course DB v0 from OpenStreetMap (leisure=golf_course, South Korea).

raw/osm_golf_course_kr.json  : Overpass `out tags geom` result
raw/HangJeongDong_*.geojson  : administrative dong boundaries (github.com/vuski/admdongkor), for 시도/시군구
Outputs: courses.json (app/search index), courses.csv (review sheet).
Licence data (행정안전부 등록체육시설업) is merged in a later step once an API key is available.
"""
import json, re, glob, os, sys, time, statistics, urllib.request, urllib.parse, csv, random
import shapely
from shapely.geometry import Polygon, LineString, Point, shape
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree
from pyproj import Transformer
from hangul import choseong, normalize, kma_grid

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_HA = 10.0          # smaller polygons are practice ranges, park golf, pitch & putt
MERGE_M = 250.0        # polygons of one club closer than this are merged
NOISE = re.compile(r"파크\s*골프|연습장|스크린|골프존|게이트볼|practice|driving range|park golf", re.I)
COURSE_PART = re.compile(r"[\s\-–]*(\(?[A-Za-z가-힣]{1,8}\s*코스\)?|(east|west|south|north|old|new|lake|valley|mountain|public)\s+course)\s*$", re.I)
TO_M = Transformer.from_crs(4326, 5179, always_xy=True)
TO_LL = Transformer.from_crs(5179, 4326, always_xy=True)
proj = lambda g, t: shapely.transform(g, lambda c: __import__("numpy").column_stack(t.transform(c[:, 0], c[:, 1])))

def club_name(name: str) -> str:
    s = re.sub(r"\s*\([^)]*[가-힣][^)]*\)\s*$", "", name.strip())   # trailing Korean note in parentheses
    for _ in range(2):
        s = COURSE_PART.sub("", s).strip()
    m = re.match(r"^(.*[가-힣][가-힣A-Za-z0-9&·\s]*?)\s*[\(\s]\s*[A-Za-z][A-Za-z\s\-&'.]+\)?$", s)   # "서원힐스CC Seowon Hills CC"
    return (m.group(1).strip() if m else s).strip(" -")

def load_polygons():
    els = json.load(open(os.path.join(HERE, "raw", "osm_golf_course_kr.json")))["elements"]
    out = []
    for e in els:
        tg = e.get("tags", {}); g = None
        if e["type"] == "way" and "geometry" in e:
            pts = [(p["lon"], p["lat"]) for p in e["geometry"]]
            if len(pts) >= 4 and pts[0] == pts[-1]: g = Polygon(pts)
        elif e["type"] == "relation":
            lines = [LineString([(p["lon"], p["lat"]) for p in m["geometry"]]) for m in e.get("members", [])
                     if m["type"] == "way" and m.get("role") != "inner" and len(m.get("geometry", [])) > 1]
            polys = list(polygonize(unary_union(lines))) if lines else []
            if polys: g = unary_union(polys)
        if g is None: continue
        if not g.is_valid: g = g.buffer(0)
        gm = proj(g, TO_M)
        name = tg.get("name:ko") or tg.get("name", "")
        if gm.area / 1e4 < MIN_HA or NOISE.search(name): continue
        out.append(dict(osm=f'{e["type"][0]}{e["id"]}', name=name, name_en=tg.get("name:en", ""), old=tg.get("old_name", ""),
                        website=tg.get("website", ""), gm=gm))
    return out

def cluster(polys):
    parent = list(range(len(polys)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    geoms = [p["gm"] for p in polys]; tree = STRtree(geoms)
    base = [normalize(club_name(p["name"])) if p["name"] else "" for p in polys]
    for i, g in enumerate(geoms):
        for j in tree.query(g.buffer(MERGE_M)):
            if j <= i or g.distance(geoms[j]) > MERGE_M: continue
            a, b = base[i], base[j]
            if not a or not b or a in b or b in a: parent[find(i)] = find(int(j))
    groups = {}
    for i in range(len(polys)): groups.setdefault(find(i), []).append(polys[i])
    return list(groups.values())

def elevations(points):
    """Copernicus GLO-90 DEM via Open-Meteo elevation API, 100 points per call (curl honours the session proxy)."""
    import subprocess
    out = []
    for i in range(0, len(points), 100):
        chunk = points[i:i + 100]
        q = urllib.parse.urlencode({"latitude": ",".join(f"{la:.5f}" for la, lo in chunk), "longitude": ",".join(f"{lo:.5f}" for la, lo in chunk)})
        for attempt in range(5):
            r = subprocess.run(["curl", "-sS", "-m", "60", "https://api.open-meteo.com/v1/elevation?" + q], capture_output=True, text=True)
            try:
                vals = json.loads(r.stdout)["elevation"]; assert len(vals) == len(chunk); out += vals; break
            except Exception:
                if attempt == 4: raise RuntimeError(f"elevation API failed: {r.stdout[:200]} {r.stderr[:200]}")
                time.sleep(3 * (attempt + 1))
        time.sleep(0.3)
    return out

def main():
    random.seed(20261111)
    polys = load_polygons(); groups = cluster(polys)
    adm_file = sorted(glob.glob(os.path.join(HERE, "raw", "HangJeongDong_*.geojson")))[-1]
    feats = json.load(open(adm_file))["features"]
    adm_geoms = [shape(f["geometry"]) for f in feats]; adm_tree = STRtree(adm_geoms)
    recs, samples = [], []
    for grp in groups:
        gm = unary_union([p["gm"] for p in grp])
        named = sorted([p for p in grp if p["name"]], key=lambda p: -p["gm"].area)
        main_p = named[0] if named else max(grp, key=lambda p: p["gm"].area)
        c = gm.centroid if gm.contains(gm.centroid) else gm.representative_point()
        lon, lat = TO_LL.transform(c.x, c.y)
        pts = [(lat, lon)]
        minx, miny, maxx, maxy = gm.bounds; tries = 0
        while len(pts) < 9 and tries < 400:
            tries += 1; p = Point(random.uniform(minx, maxx), random.uniform(miny, maxy))
            if gm.contains(p):
                lo, la = TO_LL.transform(p.x, p.y); pts.append((la, lo))
        k = adm_tree.query(Point(lon, lat), predicate="within")
        if len(k) == 0: k = [adm_tree.nearest(Point(lon, lat))]
        pr = feats[int(k[0])]["properties"]
        name = club_name(main_p["name"]) if main_p["name"] else ""
        names = sorted({club_name(p["name"]) for p in named} - {name})
        aliases = sorted({a for a in names + [p["name_en"] for p in grp] + [p["old"] for p in grp] if a and a != name})
        nx, ny = kma_grid(lat, lon)
        flags = []
        if not name: flags.append("이름없음")
        if gm.area / 1e4 < 25: flags.append("소형(25ha 미만)")
        if name and not re.search(r"[가-힣]", name): flags.append("한글명없음")
        if len(grp) > 1 and len({normalize(club_name(p["name"])) for p in named}) > 1: flags.append("병합확인")
        recs.append(dict(id="osm-" + main_p["osm"], name=name, name_norm=normalize(name) if name else "",
                         choseong=choseong(normalize(name)) if name else "", aliases=aliases,
                         sido=pr.get("sidonm", ""), sigungu=(pr.get("sggnm", "") or "").replace(pr.get("sidonm", "") + " ", ""),
                         lat=round(lat, 5), lon=round(lon, 5), area_ha=round(gm.area / 1e4, 1), n_polygons=len(grp),
                         kma_nx=nx, kma_ny=ny, website=main_p["website"], osm_ids=sorted(p["osm"] for p in grp), review=flags,
                         _n=len(pts)))
        samples += pts
    elev = elevations(samples); i = 0
    for r in recs:
        n = r.pop("_n"); vals = elev[i:i + n]; i += n
        r["elevation_m"] = round(statistics.median(vals)); r["elevation_range_m"] = [round(min(vals)), round(max(vals))]
    seen = {}
    for r in recs:
        if r["name_norm"]: seen.setdefault(r["name_norm"], []).append(r)
    for rs in seen.values():
        if len(rs) > 1:
            for r in rs: r["review"].append("동명")
    recs.sort(key=lambda r: (r["sido"], r["sigungu"], r["name"]))
    meta = dict(version="v0-osm", generated=time.strftime("%Y-%m-%d"), source="OpenStreetMap contributors (ODbL), leisure=golf_course",
                admin_boundaries=os.path.basename(adm_file), elevation="Copernicus GLO-90 via Open-Meteo", min_area_ha=MIN_HA, count=len(recs))
    json.dump(dict(meta=meta, courses=recs), open(os.path.join(HERE, "courses.json"), "w"), ensure_ascii=False, indent=1)
    with open(os.path.join(HERE, "courses.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); cols = ["id", "name", "choseong", "sido", "sigungu", "lat", "lon", "elevation_m", "area_ha", "n_polygons", "kma_nx", "kma_ny", "aliases", "review"]
        w.writerow(cols)
        for r in recs: w.writerow([("; ".join(r[c]) if isinstance(r[c], list) else r[c]) for c in cols])
    print(json.dumps(meta, ensure_ascii=False))

if __name__ == "__main__":
    main()
