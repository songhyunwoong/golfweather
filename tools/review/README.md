# 골프장 DB 검수 화면

- 게시 주소: https://claude.ai/artifact/MRMdqVM8GArQRHcANip5jh (비공개)
- `template.html` + `data/golf-courses/` 결과물 → `python3 make.py` → `review.html`
- 판정은 아티팩트 저장소에 남는다. `reviews`(골프장별)와 `licences`(위치를 못 정한 인허가별) 두 묶음이다.

## 판정 값

| 묶음 | verdict | 함께 저장되는 값 |
|---|---|---|
| reviews | `ok`, `fix`, `duplicate`, `remove` | `name`, `lat`, `lon`, `type`, `dup_of`, `note` |
| licences | `attach`, `new`, `exclude` | `course_id`, `lat`, `lon`, `note` |

검수가 끝나면 이 값을 읽어 `data/golf-courses/overrides.json`으로 저장하고 `build.py`가 반영하도록 한다(다음 단계).
