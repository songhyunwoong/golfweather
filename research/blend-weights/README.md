# 블렌드 가중치 실험

예보 기간(며칠 뒤)과 항목(기온·바람·비)별로 모델 가중치를 다르게 주면 지금의 단순 평균·중앙값 블렌드보다 나아지는지 확인한다.

## 자료

| 경로 | 기간 | 상태 |
|---|---|---|
| 공개 실행 자료(`s3://openmeteo/data_run`, 키 불필요) | 2026-08-10 이후, 1~15일 뒤 | 사용 |
| Open-Meteo 과거 실행 API(1년, 11개 모델) | 2025-11 이후, 1~7일 뒤 | 유료 Professional 요금제 이상에서만 열려 보류 |

## 실행

```bash
cd pipeline && python3 verify.py --start 2026-08-10 --end 2026-11-10 --no-write --dump /tmp/dump
```

```bash
cd research/blend-weights && python3 pairs_dump.py /tmp/dump
```

```bash
python3 experiment.py
```

| 파일 | 내용 |
|---|---|
| `pairs_dump.py` | `verify.py --dump`가 실행별로 남긴 예보·관측을 `pairs_runs.npz`로 묶는다 |
| `experiment.py` | 직전 `TRAIN_DAYS`일로 가중치·치우침을 정해 다음 `TEST_DAYS`일을 채점하는 굴림 평가. 결과는 `results/` |
| `fetch_prev.py`, `pairs_prev.py`, `stations_obs.json` | 과거 실행 API용(보류). 요금제가 맞으면 그대로 1년 실험에 쓸 수 있다 |
