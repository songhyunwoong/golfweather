#!/usr/bin/env bash
# Download 2026 hourly observations (NOAA GHCNh, includes KMA ASOS SYNOP/BUFR and airport METAR) for the
# verification stations. Public AWS Open Data bucket, no key needed.
set -euo pipefail
YEAR="${1:-2026}"
DEST="$(dirname "$0")/data/obs"
mkdir -p "$DEST"
BASE="https://noaa-ghcnh-pds.s3.amazonaws.com/hourly/access/by-year/${YEAR}/psv"
STATIONS=(
  KSI0000RKSM KSI0000RKSS KSI0000RKTN                      # Seoul AB(Seongnam), Gimpo, Daegu (airports, no precip)
  KSM00047090 KSM00047100 KSM00047101 KSM00047105          # Sokcho, Daegwallyeong, Chuncheon, Gangneung
  KSM00047112 KSM00047119 KSM00047129 KSM00047130          # Incheon, Suwon, Seosan, Uljin
  KSM00047135 KSM00047136 KSM00047138 KSM00047140          # Chupungnyeong, Andong, Pohang, Gunsan
  KSM00047145 KSM00047146 KSM00047155 KSM00047156          # Cheonan, Jeonju, Changwon, Gwangju
  KSM00047159 KSM00047162 KSM00047165 KSM00047168          # Busan, Tongyeong, Mokpo, Yeosu
  KSM00047170 KSM00047172 KSM00047177 KSM00047184          # Wando, Gochang, Hongseong, Jeju
  KSM00047188 KSM00047189 KSU047127-1 KSU047131-1 KSU047137-1  # Seongsan, Seogwipo, Chungju, Cheongju, Sangju
)
for s in "${STATIONS[@]}"; do
  curl -fsS -m 120 -o "$DEST/$s.psv" "$BASE/GHCNh_${s}_${YEAR}.psv" &
done
wait
ls -la "$DEST"
