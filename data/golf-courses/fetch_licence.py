"""Download 행정안전부 생활_골프장 조회서비스 (data.go.kr 15154978, golf course licence records)
to raw/licence_golf_courses.json. Needs DATA_GO_KR_SERVICE_KEY in the environment."""
import json, os, subprocess, sys, time, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://apis.data.go.kr/1741000/golf_courses/info"

def page(no, rows=500):
    q = urllib.parse.urlencode({"serviceKey": os.environ["DATA_GO_KR_SERVICE_KEY"], "pageNo": no, "numOfRows": rows, "type": "json"})
    for attempt in range(4):
        r = subprocess.run(["curl", "-sS", "-m", "90", f"{URL}?{q}"], capture_output=True, text=True)  # curl honours the session proxy
        try:
            return json.loads(r.stdout)["response"]["body"]
        except Exception:
            if attempt == 3: sys.exit(f"API error: {r.stdout[:300]} {r.stderr[:200]}")
            time.sleep(3 * (attempt + 1))

def main():
    items, no = [], 1
    while True:
        b = page(no); got = b["items"]["item"] if b.get("items") else []
        got = got if isinstance(got, list) else [got]
        items += got
        if not got or len(items) >= int(b["totalCount"]): break
        no += 1
    out = dict(fetched=time.strftime("%Y-%m-%d"), source="행정안전부_생활_골프장 조회서비스 (data.go.kr 15154978)", total=len(items), items=items)
    json.dump(out, open(os.path.join(HERE, "raw", "licence_golf_courses.json"), "w"), ensure_ascii=False, indent=0)
    print("rows", len(items))

if __name__ == "__main__":
    main()
