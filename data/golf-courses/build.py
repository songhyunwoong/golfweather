"""Golf course DB v1: licence records (행정안전부 생활_골프장, data.go.kr 15154978) joined to
OpenStreetMap course areas (leisure=golf_course).

raw/licence_golf_courses.json : fetch_licence.py
raw/osm_golf_course_kr.json   : Overpass `out tags geom` result
raw/HangJeongDong_*.geojson   : administrative dong boundaries (github.com/vuski/admdongkor)
Outputs: courses.json (app/search index), courses.csv (review sheet), unresolved_licences.csv.

Official name, status and membership type come from the licence data; area, centroid and elevation from OSM.
"""
import json, math, re, glob, os, sys, time, statistics, urllib.request, urllib.parse, csv, random
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


TM5174 = Transformer.from_crs("+proj=tmerc +lat_0=38 +lon_0=127.0028902777778 +k=1 +x_0=200000 +y_0=500000 +ellps=bessel +units=m "
                              "+towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43", 5179, always_xy=True)
MATCH_M = 800.0        # a licence point (usually the clubhouse address) this close to a course area belongs to it
CORP = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(재\)|\(사\)|\(합\)")
KIND = re.compile(r"[\s\-_(\[]*(일반)?(정규)?\s*(대중제|대중골프장|대중홀|대중|회원제골프장|회원제|비회원제|퍼블릭|public|간이골프장|간이)\s*(골프장|코스|홀)?[)\]]*\s*$", re.I)
TYPE_MAP = {"회원제": "회원제", "정규대중": "비회원제", "일반대중": "비회원제", "비회원제": "비회원제", "간이": "간이"}

def licence_name(n: str) -> str:
    s = CORP.sub("", n).strip()
    s = re.sub(r"\s*\((?![^)]*(CC|GC|골프|컨트리))[^)]*\)\s*$", "", s)
    for _ in range(2): s = KIND.sub("", s).strip()
    return re.sub(r"\s+", " ", s).strip(" -_,")

def score(a: str, b: str) -> float:
    """Name similarity 0..1: containment, else shared prefix covering most of the shorter name."""
    if not a or not b: return 0.0
    a, b = (x.replace("벨리", "밸리").replace("씨씨", "cc") for x in (a, b))
    short = min(len(a), len(b))
    if (a in b or b in a) and short >= 2: return 1.0
    k = 0
    while k < short and a[k] == b[k]: k += 1
    return k / short if k >= 3 and k / short >= 0.6 else 0.0

def compatible(a: str, b: str) -> bool:
    return score(a, b) > 0

def load_licences():
    d = json.load(open(os.path.join(HERE, "raw", "licence_golf_courses.json")))
    out = []
    for r in d["items"]:
        if r["SALS_STTS_CD"] not in ("01", "02"): continue          # 영업/정상, 휴업
        if NOISE.search(r["BPLC_NM"]) or "미사용" in r["BPLC_NM"]: continue
        nm = licence_name(r["BPLC_NM"]); pt = None
        try:
            x, y = float(r["CRD_INFO_X"]), float(r["CRD_INFO_Y"]); pt = Point(*TM5174.transform(x, y))
        except (TypeError, ValueError): pass
        addr = r.get("ROAD_NM_ADDR") or r.get("LOTNO_ADDR") or ""
        # MNG_NO is unique only within one municipality (OPN_ATMY_GRP_CD)
        out.append(dict(mng=r["MNG_NO"], uid=f'{r["OPN_ATMY_GRP_CD"]}-{r["MNG_NO"]}', raw=r["BPLC_NM"], name=nm, norm=normalize(nm), pt=pt, addr=addr, tel=r.get("TELNO") or "",
                        kind=TYPE_MAP.get(r.get("DTIL_TPBIZ_NM") or "", ""), status="휴업" if r["SALS_STTS_CD"] == "02" else "영업",
                        opened=r.get("LCPMT_YMD") or ""))
    return out, d.get("fetched", "")

def addr_region(addr):
    t = addr.split()
    if len(t) < 2: return "", ""
    sgg = t[1] + (" " + t[2] if len(t) > 2 and t[1].endswith("시") and t[2].endswith("구") else "")
    return t[0], sgg

def pick_best(cands, fn):
    """The single best-scoring candidate, or None when there is none or a tie."""
    sc = sorted(((fn(c), i) for i, c in enumerate(cands)), reverse=True)
    if not sc or (len(sc) > 1 and sc[0][0] == sc[1][0]): return None
    return cands[sc[0][1]]

def best_name(lics):
    """Shortest cleaned licence name that the others are compatible with; prefers names carrying a club suffix."""
    c = sorted({l["name"] for l in lics if l["name"]}, key=lambda n: (not re.search(r"(CC|GC|클럽|골프|리조트)", n, re.I), len(n)))
    return c[0] if c else ""

def apply_overrides(recs, samples, unresolved, region):
    """Apply the manual review (overrides.json): removals, renames, and placement of licences that had no location.
    `samples` (elevation sample points, in record order) is rebuilt to match. Returns (records, summary)."""
    path = os.path.join(HERE, "overrides.json")
    if not os.path.exists(path): return recs, {}
    ov = json.load(open(path)); n = dict(removed=0, renamed=0, confirmed=0, attached=0, added=0, excluded=0, near_existing=0, check=0)
    pts, i = {}, 0
    for r in recs: pts[id(r)] = samples[i:i + r["_n"]]; i += r["_n"]
    def uid_of(r): return r.get("_uid") or r["id"]
    look = {}
    for r in recs: look.setdefault(r["id"], []).append(r); look.setdefault(uid_of(r), []).append(r)
    kept = []
    for r in recs:
        o = ov["courses"].get(uid_of(r)) or (ov["courses"].get(r["id"]) if len(look[r["id"]]) == 1 else None)
        if not o: kept.append(r); continue
        if o["verdict"] in ("remove", "duplicate"): n["removed"] += 1; continue
        if o.get("name"):
            if r["name"] and r["name"] != o["name"]: r["aliases"] = sorted(set(r["aliases"]) | {r["name"]})
            r["name"] = o["name"]; n["renamed"] += 1
        if o.get("lat"): r["lat"], r["lon"], r["position"] = round(o["lat"], 5), round(o["lon"], 5), "검수 좌표"; pts[id(r)] = [(r["lat"], r["lon"])]
        r["review"] = [f for f in r["review"] if f == "이름없음" and not r["name"]]; r["reviewed"] = True; n["confirmed"] += 1
        kept.append(r)
    lic = {l["uid"]: l for l in unresolved}
    def attach(r, l):
        r["licence_ids"] = sorted(set(r["licence_ids"]) | {l["uid"]})
        if l["name"] and l["name"] != r["name"]: r["aliases"] = sorted(set(r["aliases"]) | {l["name"]})
        if l["kind"]: r["type"] = sorted(set(r["type"]) | {l["kind"]})
        if not r["address"]: r["address"] = l["addr"]
        if not r["tel"]: r["tel"] = l["tel"]
    def km(a, b, c, d): return math.hypot((d - b) * 111.32 * math.cos(math.radians((a + c) / 2)), (c - a) * 110.57)
    still = []
    for u, o in sorted(ov["licences"].items()):
        l = lic.get(u)
        if not l: continue
        if o["verdict"] == "exclude": n["excluded"] += 1; continue
        if o["verdict"] == "attach":
            t = [r for r in kept if uid_of(r) == o.get("course_id") or r["id"] == o.get("course_id")]
            if t: attach(t[0], l); n["attached"] += 1
            else: still.append(l)
            continue
        if o["verdict"] == "new" and o.get("lat"):
            near = sorted((km(o["lat"], o["lon"], r["lat"], r["lon"]), k) for k, r in enumerate(kept))
            sd, sg = region(o["lon"], o["lat"]); asd, asg = addr_region(l["addr"])
            if near and near[0][0] < 1.5:                       # the reviewer's point falls on a course that is already listed
                t = kept[near[0][1]]
                same_area = bool(asg) and t["sigungu"].replace(" ", "").startswith(asg.split()[0])
                if same_area or compatible(l["norm"], normalize(t["name"])) or any(compatible(l["norm"], normalize(a)) for a in t["aliases"]):
                    attach(t, l); n["near_existing"] += 1; continue
            flags = [] if (asg and sg.replace(" ", "").startswith(asg.split()[0])) or (asd and asd[:2] == sd[:2] and not asg) else ["좌표확인"]
            n["check"] += bool(flags)
            r = dict(id="lic-" + l["mng"], _uid="lic-" + l["uid"], name=l["name"], aliases=[], type=[l["kind"]] if l["kind"] else [], status=l["status"], sido=sd, sigungu=sg,
                     address=l["addr"], tel=l["tel"], lat=round(o["lat"], 5), lon=round(o["lon"], 5), position="검수 좌표", area_ha=None, website="", osm_ids=[],
                     licence_ids=[l["uid"]], match="검수", review=flags, reviewed=True, _n=1)
            pts[id(r)] = [(r["lat"], r["lon"])]; kept.append(r); n["added"] += 1
    samples[:] = [p for r in kept for p in pts[id(r)]]
    for r in kept: r["_n"] = len(pts[id(r)])
    done = set(ov["licences"])
    unresolved[:] = [l for l in unresolved if l["uid"] not in done] + still
    return kept, n

def main():
    random.seed(20261111)
    polys = load_polygons(); groups = cluster(polys)
    lics, fetched = load_licences()
    adm_file = sorted(glob.glob(os.path.join(HERE, "raw", "HangJeongDong_*.geojson")))[-1]
    feats = json.load(open(adm_file))["features"]
    adm_geoms = [shape(f["geometry"]) for f in feats]; adm_tree = STRtree(adm_geoms)
    def region(lon, lat):
        k = adm_tree.query(Point(lon, lat), predicate="within")
        if len(k) == 0: k = [adm_tree.nearest(Point(lon, lat))]
        pr = feats[int(k[0])]["properties"]
        return pr.get("sidonm", ""), (pr.get("sggnm", "") or "").replace(pr.get("sidonm", "") + " ", "")

    # ---- OSM clusters
    cl = []
    for grp in groups:
        gm = unary_union([p["gm"] for p in grp])
        named = sorted([p for p in grp if p["name"]], key=lambda p: -p["gm"].area)
        c = gm.centroid if gm.contains(gm.centroid) else gm.representative_point()
        lon, lat = TO_LL.transform(c.x, c.y)
        cl.append(dict(grp=grp, gm=gm, named=named, lon=lon, lat=lat, region=region(lon, lat), lics=[], how=set(), lics_coord=False,
                       norms={normalize(club_name(p["name"])) for p in named} | {normalize(p["name_en"]) for p in grp if p["name_en"]}))
    tree = STRtree([c["gm"] for c in cl])

    # ---- 1) licences with coordinates -> nearest course area within MATCH_M (name-compatible one wins)
    rest = []
    for l in lics:
        if l["pt"] is None: rest.append(l); continue
        cand = [(cl[int(j)]["gm"].distance(l["pt"]), int(j)) for j in tree.query(l["pt"].buffer(MATCH_M))]
        cand = sorted((d, j) for d, j in cand if d <= MATCH_M)
        if not cand: rest.append(l); continue
        pick = next((j for d, j in cand if any(compatible(l["norm"], n) for n in cl[j]["norms"])), cand[0][1])
        cl[pick]["lics"].append(l); cl[pick]["how"].add("좌표"); cl[pick]["lics_coord"] = True
    # ---- 2) remaining licences -> same 시군구 + compatible name
    rest2 = []
    for l in rest:
        sido, sgg = addr_region(l["addr"])
        cand = [c for c in cl if c["region"][1] and sgg and (c["region"][1] == sgg or c["region"][1].startswith(sgg.split()[0]))
                and any(compatible(l["norm"], n) for n in c["norms"])]
        if l["pt"] is not None and cand: cand = [c for c in cand if c["gm"].distance(l["pt"]) < 5000]
        best = pick_best(cand, lambda c: max(score(l["norm"], n) for n in c["norms"]))
        if best: best["lics"].append(l); best["how"].add("이름")
        else: rest2.append(l)
    # ---- 3) licences that share a club already placed (e.g. 회원제 + 대중홀 records of one club)
    rest3 = []
    for l in rest2:
        sido, sgg = addr_region(l["addr"])
        cand = [c for c in cl if c["lics"] and c["region"][1].startswith(sgg.split()[0] if sgg else "\0")
                and any(compatible(l["norm"], x["norm"]) for x in c["lics"])]
        best = pick_best(cand, lambda c: max(score(l["norm"], x["norm"]) for x in c["lics"]))
        if best: best["lics"].append(l); best["how"].add("이름")
        else: rest3.append(l)
    # ---- 3b) licence points near a nameless, licence-less course area (clubhouse address away from the mapped area)
    rest4 = []
    for l in rest3:
        cand = sorted((c["gm"].distance(l["pt"]), k) for k, c in enumerate(cl) if l["pt"] is not None and not c["named"] and not c["lics_coord"] and c["gm"].distance(l["pt"]) < 2000)
        if cand: cl[cand[0][1]]["lics"].append(l); cl[cand[0][1]]["how"].add("근접")
        else: rest4.append(l)
    rest3 = rest4

    recs, samples = [], []
    def add(rec, pts): rec["_n"] = len(pts); recs.append(rec); samples.extend(pts)
    for c in cl:
        gm, grp, named = c["gm"], c["grp"], c["named"]
        main_p = named[0] if named else max(grp, key=lambda p: p["gm"].area)
        pts = [(c["lat"], c["lon"])]
        minx, miny, maxx, maxy = gm.bounds; tries = 0
        while len(pts) < 9 and tries < 400:
            tries += 1; p = Point(random.uniform(minx, maxx), random.uniform(miny, maxy))
            if gm.contains(p):
                lo, la = TO_LL.transform(p.x, p.y); pts.append((la, lo))
        osm_name = club_name(main_p["name"]) if main_p["name"] else ""
        agree = [l for l in c["lics"] if any(compatible(l["norm"], n) for n in c["norms"])]
        clubby = [l for l in c["lics"] if re.search(r"(CC|GC|C\.C|G\.C|컨트리|골프|클럽)", l["name"], re.I)]
        # licence names are official; a licence name with no club word is usually the operating company
        name = best_name(agree) or best_name(clubby) or osm_name or best_name(c["lics"])
        al = {club_name(p["name"]) for p in named} | {p["name_en"] for p in grp} | {p["old"] for p in grp} | {l["name"] for l in c["lics"]}
        flags = []
        if not name: flags.append("이름없음")
        if not c["lics"]: flags.append("인허가미확인")
        if gm.area / 1e4 < 25: flags.append("소형(25ha 미만)")
        if c["lics"] and c["norms"] and not agree: flags.append("이름불일치")
        elif len(agree) < len(c["lics"]) and not all(compatible(a["norm"], b["norm"]) for a in c["lics"] for b in c["lics"]): flags.append("병합확인")
        if "근접" in c["how"]: flags.append("거리확인")
        add(dict(id="osm-" + main_p["osm"], name=name, aliases=sorted(a for a in al if a and a != name),
                 type=sorted({l["kind"] for l in c["lics"] if l["kind"]}), status=("휴업" if c["lics"] and all(l["status"] == "휴업" for l in c["lics"]) else "영업" if c["lics"] else ""),
                 sido=c["region"][0], sigungu=c["region"][1], address=next((l["addr"] for l in c["lics"] if l["addr"]), ""),
                 tel=next((l["tel"] for l in c["lics"] if l["tel"]), ""),
                 lat=round(c["lat"], 5), lon=round(c["lon"], 5), position="코스영역 중심", area_ha=round(gm.area / 1e4, 1),
                 website=main_p["website"], osm_ids=sorted(p["osm"] for p in grp), licence_ids=sorted(l["uid"] for l in c["lics"]),
                 match="+".join(sorted(c["how"])), review=flags), pts)
    # ---- 4) licences with coordinates but no course area in OSM: group records of one club, place at licence point
    left, unresolved = [l for l in rest3 if l["pt"] is not None], [l for l in rest3 if l["pt"] is None]
    used = [False] * len(left)
    for i, l in enumerate(left):
        if used[i]: continue
        g = [l]; used[i] = True
        for j in range(i + 1, len(left)):
            if not used[j] and left[j]["pt"].distance(l["pt"]) < 2000 and compatible(l["norm"], left[j]["norm"]): g.append(left[j]); used[j] = True
        x = sum(a["pt"].x for a in g) / len(g); y = sum(a["pt"].y for a in g) / len(g); lon, lat = TO_LL.transform(x, y)
        sd, sg = region(lon, lat); name = best_name(g)
        add(dict(id="lic-" + g[0]["mng"], _uid="lic-" + g[0]["uid"], name=name, aliases=sorted({a["name"] for a in g} - {name}), type=sorted({a["kind"] for a in g if a["kind"]}),
                 status="휴업" if all(a["status"] == "휴업" for a in g) else "영업", sido=sd, sigungu=sg, address=g[0]["addr"], tel=next((a["tel"] for a in g if a["tel"]), ""),
                 lat=round(lat, 5), lon=round(lon, 5), position="인허가 주소 좌표", area_ha=None, website="", osm_ids=[],
                 licence_ids=sorted(a["uid"] for a in g), match="", review=["영역없음"]), [(lat, lon)])

    recs, n_over = apply_overrides(recs, samples, unresolved, region)
    counts = {}
    for r in recs: counts[r["id"]] = counts.get(r["id"], 0) + 1
    for r in recs:                      # ids stay short unless two municipalities reuse a management number
        uid = r.pop("_uid", None)
        if counts[r["id"]] > 1 and uid: r["id"] = uid
    assert len({r["id"] for r in recs}) == len(recs), "duplicate course ids"
    elev = elevations(samples); i = 0
    for r in recs:
        n = r.pop("_n"); vals = elev[i:i + n]; i += n
        r["elevation_m"] = round(statistics.median(vals)); r["elevation_range_m"] = [round(min(vals)), round(max(vals))]
        r["name_norm"] = normalize(r["name"]) if r["name"] else ""; r["choseong"] = choseong(r["name_norm"])
        r["kma_nx"], r["kma_ny"] = kma_grid(r["lat"], r["lon"])
    seen = {}
    for r in recs:
        if r["name_norm"]: seen.setdefault(r["name_norm"], []).append(r)
    for rs in seen.values():
        if len(rs) > 1:
            for r in rs: r["review"].append("동명")
    recs.sort(key=lambda r: (r["sido"], r["sigungu"], r["name"]))
    meta = dict(version="v2" if n_over else "v1", generated=time.strftime("%Y-%m-%d"), count=len(recs),
                sources=["행정안전부_생활_골프장 조회서비스 (data.go.kr 15154978), " + fetched, "OpenStreetMap contributors (ODbL), leisure=golf_course"],
                admin_boundaries=os.path.basename(adm_file), elevation="Copernicus GLO-90 via Open-Meteo", min_area_ha=MIN_HA,
                licences_active=len(lics), licences_unresolved=len(unresolved), review=n_over)
    json.dump(dict(meta=meta, courses=recs), open(os.path.join(HERE, "courses.json"), "w"), ensure_ascii=False, indent=1)
    cols = ["id", "name", "choseong", "type", "status", "sido", "sigungu", "lat", "lon", "position", "elevation_m", "area_ha", "kma_nx", "kma_ny", "address", "tel", "aliases", "match", "review"]
    with open(os.path.join(HERE, "courses.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(cols)
        for r in recs: w.writerow([("; ".join(r[c]) if isinstance(r[c], list) else r[c]) for c in cols])
    with open(os.path.join(HERE, "unresolved_licences.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["관리번호", "사업장명", "구분", "주소", "전화"])
        for l in sorted(unresolved, key=lambda l: l["addr"]): w.writerow([l["uid"], l["raw"], l["kind"], l["addr"], l["tel"]])
    print(json.dumps(meta, ensure_ascii=False))

if __name__ == "__main__":
    main()
