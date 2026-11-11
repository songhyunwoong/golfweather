"""Build the review page (review.html) from data/golf-courses outputs."""
import json, csv, os
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "..", "..", "data", "golf-courses")
cs = json.load(open(os.path.join(D, "courses.json")))["courses"]
keep = ["id", "name", "aliases", "type", "status", "sido", "sigungu", "address", "tel", "lat", "lon", "position", "area_ha", "elevation_m", "review", "match", "choseong"]
courses = [dict({k: c[k] for k in keep}, licences=len(c["licence_ids"])) for c in cs]
un = [dict(id=r["관리번호"], name=r["사업장명"], kind=r["구분"], addr=r["주소"], tel=r["전화"]) for r in csv.DictReader(open(os.path.join(D, "unresolved_licences.csv"), encoding="utf-8-sig"))]
data = json.dumps(dict(courses=courses, unresolved=un), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
html = open(os.path.join(HERE, "template.html"), encoding="utf-8").read().replace("__DATA__", data)
open(os.path.join(HERE, "review.html"), "w", encoding="utf-8").write(html)
print(len(courses), len(un), len(html))
