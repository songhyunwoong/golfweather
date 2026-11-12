# 블렌드 가중치 실험 (1년 자료)

예보 기간(1~7일)과 항목(기온·바람·비)별로 모델 가중치를 다르게 주면 지금의 단순 평균·중앙값 블렌드보다 나아지는지 확인한다.

| 파일 | 내용 |
|---|---|
| `stations_obs.json` | 관측이 있는 기상청 지점 32곳 |
| `fetch_prev.py` | Open-Meteo 과거 실행 API에서 2025-11-07~2026-11-06, 11개 모델의 예보를 14일 묶음으로 받는다. `OPEN_METEO_API_KEY`가 있으면 유료 서버를 쓴다. 이어받기 가능 |
| `pairs_prev.py` | 받은 예보와 기상청 관측(`KMA_APIHUB_KEY`)을 맞춰 `pairs_main.npz`, `pairs_extra.npz`를 만든다 |

`data/`(원본 응답)는 커밋하지 않는다. `pairs_*.npz`만 커밋한다.
