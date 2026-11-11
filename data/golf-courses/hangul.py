"""Hangul helpers for course-name normalisation and chosung (초성) search keys."""
import re
CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
# longest first; stripped from the end of a name to get the searchable base name
SUFFIXES = ["컨트리클럽", "골프앤리조트", "골프&리조트", "골프리조트", "골프클럽", "골프장", "퍼블릭", "리조트",
            "country club", "golf club", "golf course", "c.c.", "c.c", "g.c", "cc", "gc"]

def choseong(s: str) -> str:
    out = []
    for ch in s:
        n = ord(ch) - 0xAC00
        out.append(CHO[n // 588] if 0 <= n <= 11171 else ch)
    return "".join(out)

def normalize(name: str) -> str:
    s = re.sub(r"\(.*?\)", "", name).lower()
    s = re.sub(r"[\s\-·_.,&']+", "", s) if not re.search(r"c\.c|g\.c", s) else re.sub(r"[\s\-·_,&']+", "", s)
    changed = True
    while changed and len(s) > 2:
        changed = False
        for suf in SUFFIXES:
            suf2 = suf.replace(" ", "")
            if s.endswith(suf2) and len(s) - len(suf2) >= 2:
                s = s[: -len(suf2)]; changed = True; break
    return s.replace(".", "")

def kma_grid(lat: float, lon: float):
    """KMA 단기예보 5 km Lambert grid (nx, ny) — official DFS conversion."""
    import math
    RE, GRID, SLAT1, SLAT2, OLON, OLAT, XO, YO = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    D = math.pi / 180
    re_, s1, s2, olon, olat = RE / GRID, SLAT1 * D, SLAT2 * D, OLON * D, OLAT * D
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(math.tan(math.pi / 4 + s2 / 2) / math.tan(math.pi / 4 + s1 / 2))
    sf = math.tan(math.pi / 4 + s1 / 2) ** sn * math.cos(s1) / sn
    ro = re_ * sf / math.tan(math.pi / 4 + olat / 2) ** sn
    ra = re_ * sf / math.tan(math.pi / 4 + lat * D / 2) ** sn
    th = lon * D - olon
    th = th - 2 * math.pi if th > math.pi else th + 2 * math.pi if th < -math.pi else th
    th *= sn
    return int(ra * math.sin(th) + XO + 0.5), int(ro - ra * math.cos(th) + YO + 0.5)

if __name__ == "__main__":
    assert choseong("베어크리크") == "ㅂㅇㅋㄹㅋ"
    assert normalize("베어크리크 골프클럽") == "베어크리크" and normalize("남서울CC") == "남서울" and normalize("안양 C.C.") == "안양"
    assert kma_grid(37.5665, 126.9780) == (60, 127), kma_grid(37.5665, 126.9780)   # 서울 중구
    assert kma_grid(33.4996, 126.5312) == (53, 38), kma_grid(33.4996, 126.5312)    # 제주시
    print("ok")
