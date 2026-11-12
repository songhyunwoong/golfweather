/* 골프날씨 — static single-page app. Data: data/courses.json, data/meta.json, data/fc/{id}.json, reliability.json */
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const app = $("#app");
  const CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ", DOW = "일월화수목금토";
  const MODELS = [["icon", "ICON"], ["blend", "블렌드"], ["ifs", "ECMWF"], ["aifs", "AIFS"], ["ukmo", "UKMO"], ["gfs", "GFS"], ["kma", "기상청"]];
  const FULL = { icon: "DWD ICON (독일)", blend: "블렌드 (ICON·AIFS·ECMWF·UKMO)", ifs: "ECMWF IFS (유럽)", aifs: "ECMWF AIFS (유럽, AI)", ukmo: "UKMO (영국)", gfs: "NCEP GFS (미국)", kma: "기상청 단기예보" };
  const VOTERS = ["icon", "ifs", "aifs", "gfs", "ukmo"];
  const ROUND_H = 5, WET_MM = 0.5;

  // ---------------- storage (per device)
  const store = {
    get(k, d) { try { const v = localStorage.getItem("gw." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("gw." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  };
  const S = { courses: [], byId: new Map(), meta: null, rel: null, model: store.get("model", "icon"), tee: store.get("tee", 7), step: store.get("step", 3), date: null, fc: null, cur: null, hour: null, cache: new Map() };

  // ---------------- helpers
  const fmt = (v, n = 0) => v == null || Number.isNaN(v) ? "—" : v.toFixed(n);
  const dirName = d => d == null ? "" : ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][Math.round(d / 45) % 8];
  const dow = d => new Date(d + "T12:00:00Z").getUTCDay();
  const dlabel = d => +d.slice(5, 7) + "/" + +d.slice(8, 10) + " (" + DOW[dow(d)] + ")";
  const todayKST = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
  const norm = s => s.toLowerCase().replace(/\s+/g, "");
  function wx(code, p) {
    const wet = (p != null && p >= 0.1) || (code >= 51 && code < 70) || (code >= 80 && code < 83) || code >= 95, snow = code >= 71 && code < 78;
    const cloud = '<path class="cloud" d="M7 18a4 4 0 0 1 .6-7.9A5.5 5.5 0 0 1 18 9.2 4.4 4.4 0 0 1 17.5 18z"/>';
    let g;
    if (snow) g = cloud + '<circle class="flake" cx="9" cy="21" r="1.2"/><circle class="flake" cx="13" cy="22" r="1.2"/><circle class="flake" cx="17" cy="21" r="1.2"/>';
    else if (wet) g = cloud + '<path class="drop" d="M9 20l-1 3M13 20l-1 3M17 20l-1 3"/>';
    else if (code === 0 || code == null) g = '<circle class="sun" cx="12" cy="12" r="5"/>';
    else if (code <= 2) g = '<circle class="sun" cx="8.5" cy="8.5" r="4"/>' + cloud;
    else g = cloud;
    const label = snow ? "눈" : wet ? "비" : code === 0 || code == null ? "맑음" : code <= 2 ? "구름 조금" : "흐림";
    return '<svg class="wx" viewBox="0 0 24 24" role="img" aria-label="' + label + '">' + g + "</svg>";
  }
  async function getFc(id) {
    if (S.cache.has(id)) return S.cache.get(id);
    const r = await fetch("data/fc/" + encodeURIComponent(id) + ".json"); if (!r.ok) throw new Error("fc");
    const j = await r.json(); S.cache.set(id, j); return j;
  }
  const dates = fc => [...new Set(fc.time.map(t => t.slice(0, 10)))];
  const idxOf = (fc, date) => fc.time.reduce((a, t, i) => (t.slice(0, 10) === date && a.push(i), a), []);
  function dayStats(fc, m, date) {
    const ix = idxOf(fc, date), pick = k => ix.map(i => m[k] ? m[k][i] : null).filter(v => v != null);
    const t = pick("t"), p = pick("p"), w = pick("w");
    const dayIx = ix.filter(i => { const h = +fc.time[i].slice(11, 13); return h >= 6 && h <= 18; });
    const wcs = dayIx.map(i => m.wc ? m.wc[i] : null).filter(v => v != null).sort((a, b) => a - b);
    const pd = dayIx.map(i => m.p[i]).filter(v => v != null).reduce((a, b) => a + b, 0);
    return { n: t.length, tmin: t.length ? Math.min(...t) : null, tmax: t.length ? Math.max(...t) : null, p: p.length ? p.reduce((a, b) => a + b, 0) : null, w: w.length ? Math.max(...w) : null,
      wc: wcs.length ? (pd >= 1 ? 61 : wcs[wcs.length >> 1]) : null };
  }
  function roundStats(fc, m, date, tee) {
    const i0 = fc.time.indexOf(date + "T" + String(tee).padStart(2, "0") + ":00"); if (i0 < 0) return null;
    const ix = []; for (let i = i0; i <= i0 + ROUND_H && i < fc.time.length; i++) ix.push(i);
    const pick = k => ix.map(i => m[k] ? m[k][i] : null).filter(v => v != null);
    const t = pick("t"); if (!t.length) return null;
    const pix = ix.slice(1), p = pix.map(i => m.p[i]).filter(v => v != null), w = pick("w"), g = pick("g"), ap = pick("ap");
    const wds = ix.map(i => [m.wd[i], m.w[i]]).filter(x => x[0] != null && x[1] != null);
    const x = wds.reduce((a, [d, s]) => a + s * Math.sin(d * Math.PI / 180), 0), y = wds.reduce((a, [d, s]) => a + s * Math.cos(d * Math.PI / 180), 0);
    return { p: p.length ? p.reduce((a, b) => a + b, 0) : null, pmax: p.length ? Math.max(...p) : null, tmin: Math.min(...t), tmax: Math.max(...t), ap: ap.length ? Math.min(...ap) : null,
      w: w.length ? w.reduce((a, b) => a + b, 0) / w.length : null, wmax: w.length ? Math.max(...w) : null, g: g.length ? Math.max(...g) : null,
      wd: wds.length ? (Math.atan2(x, y) * 180 / Math.PI + 360) % 360 : null };
  }
  function votes(fc, date, tee) {
    let wet = 0, n = 0;
    VOTERS.forEach(k => { const m = fc.models[k]; if (!m) return; const r = roundStats(fc, m, date, tee); if (!r || r.p == null) return; n++; if (r.p >= WET_MM) wet++; });
    return [wet, n];
  }
  const leadDay = date => Math.max(1, Math.round((Date.parse(date + "T00:00:00Z") - Date.parse(todayKST() + "T00:00:00Z")) / 864e5) + 1);
  function nextSaturday(ds) { return ds.find(d => dow(d) === 6) || ds[0]; }

  // ---------------- search
  function search(raw) {
    const q = norm(raw); if (!q) return [];
    const choLike = [...q].every(c => CHO.includes(c) || /[0-9a-z]/.test(c)) && [...q].some(c => CHO.includes(c));
    const out = [];
    for (const c of S.courses) {
      let s = 0, hit = "";
      if (c.norm === q) s = 100; else if (c.norm.startsWith(q)) s = 90; else if (choLike && c.cho.startsWith(q)) s = 80;
      else if (c.norm.includes(q)) s = 60; else if (choLike && c.cho.includes(q)) s = 50;
      else { const a = c.aliases.find(a => norm(a).includes(q)); if (a) { s = 55; hit = a; } else if (norm(c.sido + c.sigungu).includes(q)) s = 20; }
      if (s) out.push({ s, c, hit });
    }
    return out.sort((a, b) => b.s - a.s || a.c.name.localeCompare(b.c.name, "ko")).slice(0, 10);
  }
  const itemHtml = (c, extra) => '<li><a class="item" href="#c/' + encodeURIComponent(c.id) + '"><span><b>' + esc(c.name) + "</b></span><small>" + esc(c.sido + " " + c.sigungu) + (extra ? " · " + esc(extra) : "") + "</small></a></li>";

  // ---------------- home
  function renderHome() {
    document.title = "골프날씨";
    const favs = store.get("favs", []).map(id => S.byId.get(id)).filter(Boolean), rec = store.get("recent", []).map(id => S.byId.get(id)).filter(Boolean).filter(c => !favs.includes(c)).slice(0, 5);
    app.innerHTML = '<input class="search" id="q" type="search" placeholder="골프장 이름 또는 초성, 예: ㅂㅇㅋㄹㅋ" autocomplete="off" aria-label="골프장 검색">' +
      '<ul class="list" id="results"></ul><div id="home-rest">' +
      (favs.length ? '<section class="card"><h2>즐겨찾기</h2><div class="fav-grid" id="favs">' + favs.map(c => '<a class="fav" href="#c/' + encodeURIComponent(c.id) + '" data-fav="' + esc(c.id) + '"><b>' + esc(c.name) + '</b><small class="muted">' + esc(c.sigungu) + '</small><div class="days"><div class="muted small">불러오는 중…</div></div></a>').join("") + "</div></section>" : "") +
      (rec.length ? '<section class="card"><h2>최근 본 골프장</h2><ul class="list">' + rec.map(c => itemHtml(c)).join("") + "</ul></section>" : "") +
      (!favs.length && !rec.length ? '<section class="card"><h2>이렇게 써 보세요</h2><div class="prose small"><ul><li>검색창에 골프장 이름이나 초성을 넣으세요. <b>ㅂㅇㅋㄹㅋ</b>처럼 초성만 넣어도 됩니다.</li><li>골프장을 고르면 7일 예보를 모델별로 볼 수 있습니다.</li><li>별표를 누르면 이 화면에 즐겨찾기로 나옵니다.</li></ul></div></section>' : "") + "</div>";
    const q = $("#q"), res = $("#results");
    q.addEventListener("input", () => {
      const r = search(q.value); $("#home-rest").hidden = !!q.value.trim();
      res.innerHTML = r.length ? r.map(x => itemHtml(x.c, x.hit ? "다른 이름: " + x.hit : "")).join("") : q.value.trim() ? '<li class="muted small">찾는 골프장이 없습니다. 이름의 일부나 초성으로 다시 찾아보세요.</li>' : "";
    });
    favs.forEach(async c => {
      const box = $('[data-fav="' + CSS.escape(c.id) + '"] .days'); if (!box) return;
      try {
        const fc = await getFc(c.id), m = fc.models[S.model] || fc.models.blend || fc.models.icon, ds = dates(fc);
        const pick = ds.filter(d => dow(d) === 6 || dow(d) === 0).slice(0, 2);
        box.innerHTML = pick.map(d => { const s = dayStats(fc, m, d); return "<div><span>" + dlabel(d) + " " + wx(s.wc, null) + '</span><span class="num">' + fmt(s.tmin) + "° / " + fmt(s.tmax) + "° · " + (s.p == null ? "—" : s.p.toFixed(1) + " mm") + "</span></div>"; }).join("") || '<div class="muted small">주말 예보 없음</div>';
      } catch (e) { box.innerHTML = '<div class="muted small">예보를 불러오지 못했습니다</div>'; }
    });
  }

  // ---------------- detail
  async function renderCourse(id) {
    const c = S.byId.get(id);
    if (!c) { app.innerHTML = '<p>골프장을 찾지 못했습니다. <a href="#/">처음으로</a></p>'; return; }
    S.cur = c; document.title = c.name + " · 골프날씨";
    app.innerHTML = '<p class="muted">예보를 불러오는 중…</p>';
    try { S.fc = await getFc(id); } catch (e) { app.innerHTML = '<p>이 골프장의 예보를 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요. <a href="#/">처음으로</a></p>'; return; }
    const rec = store.get("recent", []).filter(x => x !== id); rec.unshift(id); store.set("recent", rec.slice(0, 8));
    if (!S.fc.models[S.model]) S.model = S.fc.models.icon ? "icon" : "blend";
    const ds = dates(S.fc); S.date = nextSaturday(ds); S.hour = null;
    drawCourse();
  }
  function relBadge(model, date) {
    const d = leadDay(date);
    if (model === "kma") return '<div class="badge"><b>신뢰도 검증 준비 중</b><span class="small muted">기상청 단기예보는 아직 과거 예보를 모으는 중입니다.</span></div>';
    const r = S.rel && S.rel.models[model] && S.rel.models[model][d];
    if (!r) return '<div class="badge"><b>이 리드타임의 검증 자료 없음</b></div>';
    return '<details class="badge"><summary><span class="stars" aria-label="별 ' + r.stars + '개">' + "★".repeat(r.stars) + "☆".repeat(5 - r.stars) + "</span> <b>신뢰도 " + r.score + "점</b> <span class=\"small muted\">" + d + "일 뒤 예보 기준</span></summary>" +
      '<span class="small">기온 평균오차 ' + r.t_mae.toFixed(1) + "°C · 풍속 평균오차 " + r.w_mae.toFixed(1) + " m/s · 12시간 강수 적중(ETS) " + r.ets.toFixed(2) + "</span>" +
      '<span class="small muted">' + esc(S.rel.period) + " · " + esc(S.rel.note) + ' · <a href="#info">산출 방법</a></span></details>';
  }
  function drawCourse() {
    const c = S.cur, fc = S.fc, m = fc.models[S.model], ds = dates(fc).filter(d => dayStats(fc, m, d).n >= 6 || VOTERS.some(k => fc.models[k] && dayStats(fc, fc.models[k], d).n >= 6));
    if (!ds.includes(S.date)) S.date = ds[0];
    const favs = store.get("favs", []), isFav = favs.includes(c.id);
    const tees = []; for (let h = 5; h <= 15; h++) tees.push(h);
    const rs = roundStats(fc, m, S.date, S.tee), v = votes(fc, S.date, S.tee), init = S.meta.models[S.model] && S.meta.models[S.model].init;
    const st = dayStats(fc, m, S.date), noData = st.n < 6;
    const native = nativeStep(S.model, S.date);
    let h = '<div class="row between"><div><h1>' + esc(c.name) + '</h1><p class="muted small">' + esc(c.sido + " " + c.sigungu) + " · 해발 " + c.elev + " m" + (c.type.length ? " · " + esc(c.type.join("·")) : "") + '</p></div><button class="star-btn" id="fav" aria-pressed="' + isFav + '">' + (isFav ? "★ 즐겨찾기" : "☆ 즐겨찾기") + "</button></div>";
    h += '<section class="card"><div class="chips" role="group" aria-label="날씨 모델">' + MODELS.filter(([k]) => fc.models[k]).map(([k, l]) => '<button class="chip" data-model="' + k + '" aria-pressed="' + (k === S.model) + '">' + l + "</button>").join("") + "</div>" +
      '<p class="small muted">' + esc(FULL[S.model]) + (init ? " · " + esc(initLabel(init)) + " 실행" : "") + "</p>" + relBadge(S.model, S.date) + "</section>";
    // days
    h += '<div class="days" role="group" aria-label="날짜">' + ds.map(d => { const s = dayStats(fc, m, d), w = dow(d);
      return '<button class="day" data-date="' + d + '" aria-pressed="' + (d === S.date) + '"><span class="dow ' + (w === 6 ? "sat" : w === 0 ? "sun" : "") + '">' + DOW[w] + '</span><span class="muted small">' + +d.slice(5, 7) + "/" + +d.slice(8, 10) + "</span>" + (s.n >= 6 ? wx(s.wc, null) + '<span class="num">' + fmt(s.tmax) + "° <span class=\"muted\">" + fmt(s.tmin) + '°</span></span><span class="pr' + (s.p >= 1 ? "" : " dry") + '">' + (s.p >= 0.1 ? s.p.toFixed(1) : "0") + "</span>" : '<span class="muted small">예보<br>없음</span>') + "</button>"; }).join("") + "</div>";
    // round summary
    h += '<section class="card"><div class="row between"><h2>' + dlabel(S.date) + ' 라운드</h2><label class="small">티업 <select class="plain" id="tee">' + tees.map(t => '<option value="' + t + '"' + (t === S.tee ? " selected" : "") + ">" + String(t).padStart(2, "0") + ":00</option>").join("") + "</select></label></div>";
    if (!rs) h += '<p class="muted">' + (noData ? "이 모델은 이 날짜의 예보가 없습니다. 다른 모델을 골라 보세요." : "이 시각의 예보가 없습니다.") + "</p>";
    else {
      const wet = rs.p != null && rs.p >= WET_MM;
      h += '<div class="summary"><div class="stat' + (wet ? " rainy" : "") + '"><span>강수 (' + ROUND_H + "시간)</span><b>" + fmt(rs.p, 1) + ' mm</b><span>시간당 최대 ' + fmt(rs.pmax, 1) + '</span></div><div class="stat"><span>기온</span><b>' + fmt(rs.tmin) + "~" + fmt(rs.tmax) + '°</b><span>체감 최저 ' + fmt(rs.ap) + '°</span></div><div class="stat"><span>바람</span><b>' + fmt(rs.w, 1) + ' m/s</b><span>' + dirName(rs.wd) + "풍 · 최대 " + fmt(rs.wmax, 1) + '</span></div><div class="stat"><span>돌풍</span><b>' + (rs.g == null ? "—" : fmt(rs.g, 1) + " m/s") + "</b><span>" + (rs.g == null ? "이 모델은 자료 없음" : "순간 최대") + "</span></div></div>";
      if (v[1] >= 2) h += '<div class="verdict' + (v[0] * 2 > v[1] ? " wet" : "") + '">' + String(S.tee).padStart(2, "0") + "~" + String(S.tee + ROUND_H).padStart(2, "0") + "시에 <b>" + v[1] + "개 모델 중 " + v[0] + "개</b>가 비(" + WET_MM + " mm 이상)를 예보합니다." + (v[0] === 0 ? " 모델들이 비 없음으로 일치합니다." : v[0] === v[1] ? " 모델들이 비로 일치합니다." : " 모델마다 다릅니다. 아래 모델 비교를 확인하세요.") + "</div>";
    }
    h += "</section>";
    // hourly
    h += '<section class="card"><div class="row between"><h2>시간별</h2><span class="seg" role="group" aria-label="시간 간격"><button data-step="1" aria-pressed="' + (S.step === 1) + '">1시간</button><button data-step="3" aria-pressed="' + (S.step === 3) + '">3시간</button></span></div>' +
      (native > 1 ? '<p class="note">이 날짜는 ' + esc(FULL[S.model].split(" (")[0]) + "의 원자료가 " + native + "시간 간격입니다. 사이 시각은 보간한 값입니다.</p>" : "") +
      (noData ? '<p class="muted">이 모델은 이 날짜의 예보가 없습니다.</p>' : '<div class="chart" id="chart"></div><div class="readout" id="readout"></div><details><summary>표로 보기</summary><div class="scroll" id="htable"></div></details>') + "</section>";
    // compare
    h += '<section class="card"><h2>' + dlabel(S.date) + ' 모델 비교</h2><div class="scroll">' + compareTable() + '</div><p class="small muted">라운드 강수는 티업 뒤 ' + ROUND_H + "시간 합계입니다. 모델 이름을 누르면 그 모델로 바뀝니다.</p></section>";
    // location
    h += '<section class="card"><h2>위치</h2><div id="map" aria-label="골프장 위치 지도"></div><p class="small muted">' + esc(c.addr || c.sido + " " + c.sigungu) + (c.tel ? " · " + esc(c.tel) : "") + '</p><div class="links"><a target="_blank" rel="noopener" href="https://map.naver.com/p/search/' + encodeURIComponent(c.name) + '">네이버 지도</a><a target="_blank" rel="noopener" href="https://map.kakao.com/link/to/' + encodeURIComponent(c.name.replace(/,/g, " ")) + "," + c.lat + "," + c.lon + '">카카오맵 길찾기</a><a target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=' + c.lat + "," + c.lon + '">구글 지도</a><a href="#map/' + encodeURIComponent(c.id) + '">날씨 지도에서 보기</a></div></section>';
    app.innerHTML = h;
    if (!noData) { drawChart(); drawTable(); }
    const sel = $('.day[aria-pressed="true"]'); if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest", inline: "center" });
    drawMap();
  }
  const initLabel = s => { if (s.endsWith("Z")) { const d = new Date(Date.parse(s.replace("Z", ":00Z")) + 9 * 3600e3); return (d.getUTCMonth() + 1) + "/" + d.getUTCDate() + " " + String(d.getUTCHours()).padStart(2, "0") + "시"; } return +s.slice(5, 7) + "/" + +s.slice(8, 10) + " " + s.slice(11, 13) + "시"; };
  function nativeStep(model, date) {
    const mm = S.meta.models[model]; if (!mm || !mm.native_steps || !mm.init) return 1;
    const lead = (Date.parse(date + "T12:00:00+09:00") - Date.parse(mm.init.replace("Z", ":00Z"))) / 3600e3; let st = 1;
    mm.native_steps.forEach(([from, s]) => { if (lead >= from) st = s; }); return st;
  }
  function compareTable() {
    const fc = S.fc; let rows = "", maxP = 1;
    const data = MODELS.filter(([k]) => fc.models[k]).map(([k, l]) => { const d = dayStats(fc, fc.models[k], S.date), r = roundStats(fc, fc.models[k], S.date, S.tee); if (r && r.p > maxP) maxP = r.p; return { k, l, d, r }; });
    data.forEach(({ k, l, d, r }) => {
      rows += '<tr class="' + (k === S.model ? "sel" : "") + '"><td><button class="chip" data-model="' + k + '" aria-pressed="' + (k === S.model) + '">' + l + "</button></td>" +
        (d.n < 6 ? '<td colspan="4" class="muted">예보 없음</td>' : "<td>" + fmt(d.tmin) + "° / " + fmt(d.tmax) + '°</td><td class="' + (d.p >= 1 ? "wet" : "") + '">' + fmt(d.p, 1) + '</td><td><span class="barcell"><i style="width:' + (r && r.p ? Math.max(3, 60 * r.p / maxP) : 0) + 'px"></i><span class="' + (r && r.p >= WET_MM ? "wet" : "") + '">' + (r ? fmt(r.p, 1) : "—") + "</span></span></td><td>" + (r ? fmt(r.w, 1) : "—") + "</td>") + "</tr>";
    });
    return '<table><thead><tr><th>모델</th><th>최저/최고</th><th>하루 강수 mm</th><th>라운드 강수 mm</th><th>라운드 바람 m/s</th></tr></thead><tbody>' + rows + "</tbody></table>";
  }

  // ---------------- chart
  function hourRows() {
    const fc = S.fc, m = fc.models[S.model], out = [];
    idxOf(fc, S.date).forEach(i => {
      const hh = +fc.time[i].slice(11, 13); if (hh % S.step) return;
      let p = null; for (let k = i - S.step + 1; k <= i; k++) if (k >= 0 && m.p[k] != null) p = (p || 0) + m.p[k];
      let g = null; if (m.g) for (let k = i - S.step + 1; k <= i; k++) if (k >= 0 && m.g[k] != null) g = Math.max(g ?? 0, m.g[k]);
      out.push({ i, h: hh, t: m.t[i], ap: m.ap ? m.ap[i] : null, p, w: m.w[i], wd: m.wd[i], g, wc: m.wc ? m.wc[i] : null, pop: m.pop ? m.pop[i] : null });
    });
    return out.filter(r => r.t != null);
  }
  function drawChart() {
    const rows = hourRows(), box = $("#chart"); if (!rows.length) { box.innerHTML = '<p class="muted">예보 없음</p>'; return; }
    const W = 360, L = 26, R = 8, T1 = 16, H1 = 84, G = 30, H2 = 46, G2 = 18, H3 = 30, H = T1 + H1 + G + H2 + G2 + H3 + 16;
    const x = h => L + (h / 24) * (W - L - R), bw = (W - L - R) / 24 * S.step;
    const ts = rows.map(r => r.t), tmin = Math.floor(Math.min(...ts) - 1), tmax = Math.ceil(Math.max(...ts) + 1), y1 = v => T1 + (tmax - v) / (tmax - tmin) * H1;
    const pmax = Math.max(2, ...rows.map(r => r.p || 0)), P0 = T1 + H1 + G + H2, y2 = v => P0 - (v / pmax) * H2, W0 = P0 + G2;
    let s = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + dlabel(S.date) + ' 시간별 기온, 강수, 바람 그래프">';
    s += '<rect class="band" x="' + x(S.tee) + '" y="' + (T1 - 8) + '" width="' + (x(S.tee + ROUND_H) - x(S.tee)) + '" height="' + (H - T1 - 6) + '" rx="4"/>';
    const step = (tmax - tmin) > 12 ? 5 : (tmax - tmin) > 6 ? 2 : 1;
    for (let v = Math.ceil(tmin / step) * step; v <= tmax; v += step) s += '<line class="gridl" x1="' + L + '" x2="' + (W - R) + '" y1="' + y1(v) + '" y2="' + y1(v) + '"/><text class="ax" x="' + (L - 4) + '" y="' + (y1(v) + 3) + '" text-anchor="end">' + v + "°</text>";
    s += '<line class="gridl" x1="' + L + '" x2="' + (W - R) + '" y1="' + P0 + '" y2="' + P0 + '"/><text class="ax" x="' + (L - 4) + '" y="' + (P0 + 3) + '" text-anchor="end">0</text><text class="ax" x="' + (L - 4) + '" y="' + (y2(pmax) + 3) + '" text-anchor="end">' + Math.round(pmax) + '</text><text class="ax" x="' + L + '" y="' + (T1 + H1 + G - H2 * 0 - 34) + '"></text>';
    s += '<text class="ax" x="' + L + '" y="' + (T1 - 6) + '">기온 °C</text><text class="ax" x="' + L + '" y="' + (P0 - H2 - 5) + '">강수 mm / ' + S.step + '시간</text><text class="ax" x="' + L + '" y="' + (W0 - 2) + '">바람 m/s</text>';
    rows.forEach(r => { if (r.p > 0.04) { const bx = x(r.h) - bw + 1, by = y2(Math.min(r.p, pmax)); s += '<rect class="pbar" x="' + bx + '" y="' + by + '" width="' + Math.max(1, bw - 2) + '" height="' + (P0 - by) + '" rx="2"/>' + (r.p >= 0.3 && (S.step === 3 || r.p >= 1) ? '<text class="plab" x="' + (bx + (bw - 2) / 2) + '" y="' + (by - 2) + '" text-anchor="middle">' + r.p.toFixed(1) + "</text>" : ""); } });
    s += '<path class="tline" d="' + rows.map((r, k) => (k ? "L" : "M") + x(r.h).toFixed(1) + " " + y1(r.t).toFixed(1)).join(" ") + '"/>';
    rows.forEach(r => { if (r.h % 3 === 0) s += '<circle class="tdot" cx="' + x(r.h) + '" cy="' + y1(r.t) + '" r="2.6"/><text class="lab" x="' + x(r.h) + '" y="' + (y1(r.t) - 6) + '" text-anchor="middle">' + Math.round(r.t) + "</text>"; });
    rows.forEach(r => { if (r.h % 3 || r.w == null) return; const cx = x(r.h), cy = W0 + 10, a = (r.wd + 180) % 360;
      s += '<g transform="translate(' + cx + " " + cy + ") rotate(" + a + ')"><path class="arrow" d="M0 6V-6M-3.2 -2.2L0 -6L3.2 -2.2"/></g><text class="lab" x="' + cx + '" y="' + (W0 + 28) + '" text-anchor="middle">' + r.w.toFixed(1) + "</text>"; });
    for (let hh = 0; hh <= 21; hh += 3) s += '<text class="ax" x="' + x(hh) + '" y="' + (H - 2) + '" text-anchor="middle">' + hh + "시</text>";
    s += '<line class="cursor" id="cur" x1="0" x2="0" y1="' + (T1 - 8) + '" y2="' + (H - 14) + '" visibility="hidden"/><rect id="hit" x="' + L + '" y="0" width="' + (W - L - R) + '" height="' + H + '" fill="transparent"/></svg>';
    box.innerHTML = s;
    const svg = $("svg", box), cur = $("#cur", box), show = hh => {
      const r = rows.reduce((a, b) => Math.abs(b.h - hh) < Math.abs(a.h - hh) ? b : a); S.hour = r.h;
      cur.setAttribute("x1", x(r.h)); cur.setAttribute("x2", x(r.h)); cur.setAttribute("visibility", "visible");
      const v = S.fc.rain_votes && S.fc.rain_votes[r.i];
      $("#readout").innerHTML = "<b>" + String(r.h).padStart(2, "0") + "시</b> " + wx(r.wc, r.p) + " 기온 " + fmt(r.t, 1) + "°" + (r.ap != null ? " (체감 " + fmt(r.ap) + "°)" : "") + " · 강수 " + fmt(r.p, 1) + " mm" + (r.pop != null ? " (확률 " + r.pop + "%)" : "") + " · 바람 " + dirName(r.wd) + " " + fmt(r.w, 1) + " m/s" + (r.g != null ? " (돌풍 " + fmt(r.g, 1) + ")" : "") + (v ? " · 비 예보 모델 " + v[0] + "/" + v[1] : "");
    };
    const at = ev => { const b = svg.getBoundingClientRect(); show(((ev.clientX - b.left) / b.width * W - L) / (W - L - R) * 24); };
    $("#hit", box).addEventListener("pointermove", at); $("#hit", box).addEventListener("pointerdown", at);
    show(S.hour != null ? S.hour : S.tee);
  }
  function drawTable() {
    const rows = hourRows();
    $("#htable").innerHTML = '<table><thead><tr><th>시각</th><th>기온</th><th>체감</th><th>강수 mm</th><th>바람 m/s</th><th>돌풍</th></tr></thead><tbody>' + rows.map(r => '<tr class="' + (r.h >= S.tee && r.h <= S.tee + ROUND_H ? "sel" : "") + '"><td>' + String(r.h).padStart(2, "0") + "시 " + wx(r.wc, r.p) + "</td><td>" + fmt(r.t, 1) + "°</td><td>" + fmt(r.ap) + '°</td><td class="' + (r.p >= 0.1 ? "wet" : "") + '">' + fmt(r.p, 1) + "</td><td>" + dirName(r.wd) + " " + fmt(r.w, 1) + "</td><td>" + fmt(r.g, 1) + "</td></tr>").join("") + "</tbody></table>";
  }

  // ---------------- map (Leaflet, loaded on demand)
  let leaflet = null;
  function loadLeaflet() {
    if (!leaflet) leaflet = new Promise((res, rej) => {
      const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"; document.head.appendChild(l);
      const s = document.createElement("script"); s.src = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"; s.onload = () => res(window.L); s.onerror = rej; document.head.appendChild(s);
    });
    return leaflet;
  }
  async function drawMap() {
    const el = $("#map"), c = S.cur; if (!el) return;
    try {
      const L = await loadLeaflet(); if (!$("#map") || S.cur !== c) return;
      const map = L.map(el, { scrollWheelZoom: false, attributionControl: true }).setView([c.lat, c.lon], 12);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(map);
      L.circleMarker([c.lat, c.lon], { radius: 9, color: "#1c6a55", weight: 3, fillColor: "#63c4a3", fillOpacity: 0.9 }).addTo(map).bindTooltip(c.name);
    } catch (e) { el.outerHTML = '<p class="muted small">지도를 불러오지 못했습니다. 아래 링크로 위치를 확인하세요.</p>'; }
  }


  // ---------------- weather map
  const MAPMODELS = [["icon", "ICON"], ["ifs", "ECMWF"], ["gfs", "GFS"]];
  const LAYERS = {
    p: { label: "강수", unit: "mm/3시간", stepped: true, stops: [[0.1, "#b9dcff"], [0.5, "#7fbcf5"], [1, "#4a97e8"], [2, "#2a78d6"], [5, "#1f55b5"], [10, "#5b3fb5"], [20, "#8e2fa8"], [40, "#c0267e"]] },
    t: { label: "기온", unit: "°C", stops: [[-15, "#2b3f9e"], [-5, "#4f8fd6"], [0, "#9fd0ea"], [5, "#d9eef0"], [10, "#f3f1d5"], [15, "#f6d98a"], [20, "#f1a652"], [25, "#e36a35"], [30, "#c6352b"], [35, "#8f1a2b"]] },
    w: { label: "바람", unit: "m/s", stops: [[0, "#f2f7f5"], [2, "#cfe8dc"], [4, "#9fd3bd"], [6, "#63b99a"], [8, "#2f9c86"], [10, "#1f7f8f"], [14, "#2a5d9c"], [18, "#4b3a94"], [24, "#6b2380"]] }
  };
  const hex = h => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  Object.values(LAYERS).forEach(l => l.rgb = l.stops.map(([v, c]) => [v, hex(c)]));
  function colorOf(layer, v) {
    const st = layer.rgb; if (v == null || Number.isNaN(v)) return null;
    if (layer.stepped) { let c = null; for (const [th, rgb] of st) if (v >= th) c = rgb; return c; }
    if (v <= st[0][0]) return st[0][1]; if (v >= st[st.length - 1][0]) return st[st.length - 1][1];
    for (let i = 1; i < st.length; i++) if (v <= st[i][0]) { const f = (v - st[i - 1][0]) / (st[i][0] - st[i - 1][0]), a = st[i - 1][1], b = st[i][1]; return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f]; }
  }
  const M = { map: null, model: store.get("mapModel", "icon"), layer: store.get("mapLayer", "p"), ti: 0, meta: null, data: {}, cache: new Map(), overlay: null, timer: null, raf: null, parts: [], pcanvas: null, alive: false };
  function decodePng(url, meta, v) {
    return new Promise((res, rej) => { const im = new Image(); im.onload = () => {
      const c = document.createElement("canvas"); c.width = im.width; c.height = im.height; const x = c.getContext("2d", { willReadFrequently: true }); x.drawImage(im, 0, 0);
      const d = x.getImageData(0, 0, im.width, im.height).data, out = new Float32Array(im.width * im.height);
      for (let i = 0, j = 0; i < out.length; i++, j += 4) { const raw = d[j] * 256 + d[j + 1]; out[i] = raw === 65535 ? NaN : raw * v.scale + v.offset; }
      res(out); }; im.onerror = rej; im.src = url; });
  }
  async function loadMapModel(key) {
    if (M.cache.has(key)) return M.cache.get(key);
    const meta = await fetch("data/map/" + key + ".json").then(r => { if (!r.ok) throw new Error("map"); return r.json(); });
    const data = {}; await Promise.all(["t", "p", "u", "v"].map(async k => { data[k] = await decodePng("data/map/" + meta.vars[k].file + "?" + encodeURIComponent(meta.init), meta, meta.vars[k]); }));
    const r = { meta, data }; M.cache.set(key, r); return r;
  }
  function sample(arr, meta, ti, lat, lon) {       // bilinear; NaN outside the grid
    const fx = (lon - meta.west) / meta.step, fy = (meta.north - lat) / meta.step;
    if (fx < 0 || fy < 0 || fx > meta.nx - 1 || fy > meta.ny - 1) return NaN;
    const x0 = Math.min(meta.nx - 2, Math.floor(fx)), y0 = Math.min(meta.ny - 2, Math.floor(fy)), ax = fx - x0, ay = fy - y0, o = ti * meta.nx * meta.ny + y0 * meta.nx + x0;
    return (arr[o] * (1 - ax) + arr[o + 1] * ax) * (1 - ay) + (arr[o + meta.nx] * (1 - ax) + arr[o + meta.nx + 1] * ax) * ay;
  }
  const fieldAt = (k, lat, lon) => k === "w" ? Math.hypot(sample(M.data.u, M.meta, M.ti, lat, lon), sample(M.data.v, M.meta, M.ti, lat, lon)) : sample(M.data[k], M.meta, M.ti, lat, lon);
  function drawField() {
    const m = M.meta, layer = LAYERS[M.layer], W = m.nx * 5, merc = la => Math.log(Math.tan(Math.PI / 4 + la * Math.PI / 360));
    const mN = merc(m.north), mS = merc(m.south), H = Math.round(W * (mN - mS) / ((m.east - m.west) * Math.PI / 180));
    const c = document.createElement("canvas"); c.width = W; c.height = H; const x = c.getContext("2d"), img = x.createImageData(W, H), d = img.data;
    for (let r = 0; r < H; r++) {
      const lat = (2 * Math.atan(Math.exp(mN - (r + 0.5) / H * (mN - mS))) - Math.PI / 2) * 180 / Math.PI;
      for (let q = 0; q < W; q++) {
        const col = colorOf(layer, fieldAt(M.layer, lat, m.west + (q + 0.5) / W * (m.east - m.west))), o = (r * W + q) * 4;
        if (col) { d[o] = col[0]; d[o + 1] = col[1]; d[o + 2] = col[2]; d[o + 3] = 255; }
      }
    }
    x.putImageData(img, 0, 0);
    const url = c.toDataURL();
    if (M.overlay) M.overlay.setUrl(url); else M.overlay = window.L.imageOverlay(url, [[m.south, m.west], [m.north, m.east]], { opacity: 0.72, interactive: false }).addTo(M.map);
  }
  function mapTimeLabel() { const t = M.meta.times[M.ti]; return dlabel(t.slice(0, 10)) + " " + t.slice(11, 13) + "시"; }
  function drawMapUi() {
    if (!M.alive || !M.meta) return;
    drawField();
    $("#mt-label").textContent = mapTimeLabel() + (M.layer === "p" ? " · 직전 3시간 합계" : "");
    $("#mt").value = M.ti; $("#mt").max = M.meta.last_valid;
    $("#m-run").textContent = M.meta.label + " · " + initLabel(M.meta.init) + " 실행";
    const l = LAYERS[M.layer];
    $("#m-legend").innerHTML = '<span class="small muted">' + l.label + " " + l.unit + "</span>" + l.stops.map(([v, c]) => '<span class="lg"><i style="background:' + c + '"></i>' + v + "</span>").join("");
    document.querySelectorAll("[data-mlayer]").forEach(b => b.setAttribute("aria-pressed", b.dataset.mlayer === M.layer));
    document.querySelectorAll("[data-mmodel]").forEach(b => b.setAttribute("aria-pressed", b.dataset.mmodel === M.model));
    if (M.probe) probe(M.probe);
    M.parts = [];
  }
  function probe(ll) {
    M.probe = ll; const t = fieldAt("t", ll.lat, ll.lng), p = fieldAt("p", ll.lat, ll.lng), u = sample(M.data.u, M.meta, M.ti, ll.lat, ll.lng), v = sample(M.data.v, M.meta, M.ti, ll.lat, ll.lng);
    $("#m-probe").innerHTML = Number.isNaN(t) ? "지도 범위 밖입니다." : "<b>" + mapTimeLabel() + "</b> 고른 지점 · 기온 " + fmt(t, 1) + "° · 강수 " + fmt(p, 1) + " mm/3시간 · 바람 " + dirName((Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360) + " " + fmt(Math.hypot(u, v), 1) + " m/s";
    if (!M.pin) M.pin = window.L.circleMarker(ll, { radius: 6, color: "#14201a", weight: 2, fillColor: "#fff", fillOpacity: 1, interactive: false }).addTo(M.map); else M.pin.setLatLng(ll);
  }
  function animate() {               // wind particles, drawn in container pixels and reset whenever the map moves
    if (!M.alive) return;
    M.raf = requestAnimationFrame(animate);
    const cv = M.pcanvas, map = M.map; if (!cv || !M.meta) return;
    const x = cv.getContext("2d"), w = cv.width, h = cv.height;
    if (M.layer !== "w" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) { if (M.parts.length || M.dirty) { x.clearRect(0, 0, w, h); M.parts = []; M.dirty = false; } return; }
    const dpr = cv.width / cv.clientWidth, N = Math.round(cv.clientWidth * cv.clientHeight / 220), b = map.getBounds(), speed = 0.028 * Math.pow(1.6, map.getZoom() - 7);
    while (M.parts.length < N) M.parts.push({ lat: b.getSouth() + Math.random() * (b.getNorth() - b.getSouth()), lng: b.getWest() + Math.random() * (b.getEast() - b.getWest()), age: Math.random() * 60 });
    x.globalCompositeOperation = "destination-in"; x.fillStyle = "rgba(0,0,0,0.9)"; x.fillRect(0, 0, w, h); x.globalCompositeOperation = "source-over";
    x.strokeStyle = "rgba(20,32,26,0.7)"; x.lineWidth = 1.1 * dpr; x.lineCap = "round"; x.beginPath(); M.dirty = true;
    for (const p of M.parts) {
      const u = sample(M.data.u, M.meta, M.ti, p.lat, p.lng), v = sample(M.data.v, M.meta, M.ti, p.lat, p.lng);
      if (Number.isNaN(u) || p.age++ > 70) { p.lat = b.getSouth() + Math.random() * (b.getNorth() - b.getSouth()); p.lng = b.getWest() + Math.random() * (b.getEast() - b.getWest()); p.age = 0; continue; }
      const a = map.latLngToContainerPoint([p.lat, p.lng]); const nx = a.x + u * speed * 8, ny = a.y - v * speed * 8;
      x.moveTo(a.x * dpr, a.y * dpr); x.lineTo(nx * dpr, ny * dpr);
      const ll = map.containerPointToLatLng([nx, ny]); p.lat = ll.lat; p.lng = ll.lng;
      if (nx < 0 || ny < 0 || nx > cv.clientWidth || ny > cv.clientHeight) p.age = 999;
    }
    x.stroke();
  }
  function stopMap() {
    M.alive = false; if (M.timer) clearInterval(M.timer); if (M.raf) cancelAnimationFrame(M.raf); M.timer = M.raf = null;
    if (M.map) { M.map.remove(); M.map = null; } M.overlay = M.pin = M.pcanvas = M.probe = null; M.parts = [];
  }
  async function setMapModel(key) {
    const st = $("#m-status"); st.hidden = false; st.textContent = "지도 자료를 불러오는 중…";
    try { const r = await loadMapModel(key); if (!M.alive) return; M.model = key; store.set("mapModel", key); M.meta = r.meta; M.data = r.data; M.ti = Math.min(M.ti, r.meta.last_valid); st.hidden = true; drawMapUi(); }
    catch (e) { st.textContent = "이 모델의 지도 자료를 불러오지 못했습니다."; }
  }
  async function renderMap(courseId) {
    document.title = "날씨 지도 · 골프날씨";
    const c = courseId ? S.byId.get(courseId) : null;
    app.innerHTML = '<div class="mapbox"><div id="wmap" aria-label="날씨 지도"></div><canvas id="wparts"></canvas></div>' +
      '<section class="card"><div class="row between"><span class="seg" role="group" aria-label="표시할 날씨">' + Object.entries(LAYERS).map(([k, l]) => '<button data-mlayer="' + k + '">' + l.label + "</button>").join("") + '</span><span class="seg" role="group" aria-label="날씨 모델">' + MAPMODELS.map(([k, l]) => '<button data-mmodel="' + k + '">' + l + "</button>").join("") + "</span></div>" +
      '<div class="row"><button class="chip" id="m-play" aria-pressed="false">▶ 재생</button><b id="mt-label" class="num"></b></div><input id="mt" type="range" min="0" max="56" step="1" value="0" aria-label="시각">' +
      '<div class="legend" id="m-legend"></div><div class="readout" id="m-probe">지도를 누르면 그 지점의 값이 나옵니다. 점은 골프장입니다.</div><p class="small muted" id="m-run"></p><p class="note" id="m-status" hidden></p></section>';
    stopMap(); M.alive = true; M.dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    let L; try { L = await loadLeaflet(); } catch (e) { $("#m-status").hidden = false; $("#m-status").textContent = "지도를 불러오지 못했습니다."; return; }
    if (!M.alive || !$("#wmap")) return;
    const map = M.map = L.map("wmap", { minZoom: 6, maxZoom: 12, maxBounds: [[31, 121], [40.5, 135]], preferCanvas: true }).setView(c ? [c.lat, c.lon] : [36.0, 127.8], c ? 10 : 7);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · 날씨: Open-Meteo' }).addTo(map);
    map.createPane("courses").style.zIndex = 450;
    const marks = [], rad = () => { const z = map.getZoom(); return z >= 10 ? 6 : z >= 9 ? 4.5 : z >= 8 ? 3 : 1.8; };
    S.courses.forEach(k => { const me = c && k.id === c.id, mk = L.circleMarker([k.lat, k.lon], { pane: "courses", radius: me ? 7 : rad(), color: "#14201a", weight: me ? 2 : 0.8, fillColor: me ? "#f6c453" : "#ffffff", fillOpacity: 0.95 }).addTo(map);
      if (!me) marks.push(mk);
      mk.bindPopup("<b>" + esc(k.name) + "</b><br>" + esc(k.sido + " " + k.sigungu) + '<br><a href="#c/' + encodeURIComponent(k.id) + '">예보 보기</a>'); });
    const cv = M.pcanvas = $("#wparts"), fit = () => { const r = cv.getBoundingClientRect(), d = Math.min(2, window.devicePixelRatio || 1); cv.width = Math.round(r.width * d); cv.height = Math.round(r.height * d); M.parts = []; };
    map.on("zoomend", () => { const r = rad(); marks.forEach(m => m.setRadius(r)); });
    fit(); map.on("move zoom resize", () => { if (cv.width) cv.getContext("2d").clearRect(0, 0, cv.width, cv.height); M.parts = []; }); map.on("resize", fit);
    map.on("click", ev => { if (M.meta) probe(ev.latlng); });
    $("#mt").addEventListener("input", ev => { M.ti = +ev.target.value; drawMapUi(); });
    $("#m-play").addEventListener("click", () => {
      const b = $("#m-play");
      if (M.timer) { clearInterval(M.timer); M.timer = null; b.textContent = "▶ 재생"; b.setAttribute("aria-pressed", "false"); return; }
      b.textContent = "❚❚ 멈춤"; b.setAttribute("aria-pressed", "true");
      M.timer = setInterval(() => { M.ti = M.ti >= M.meta.last_valid ? 0 : M.ti + 1; drawMapUi(); }, 700);
    });
    app.querySelectorAll("[data-mlayer]").forEach(b => b.addEventListener("click", () => { M.layer = b.dataset.mlayer; store.set("mapLayer", M.layer); drawMapUi(); }));
    app.querySelectorAll("[data-mmodel]").forEach(b => b.addEventListener("click", () => setMapModel(b.dataset.mmodel)));
    M.ti = 0; await setMapModel(M.model);
    if (c && M.meta) probe({ lat: c.lat, lng: c.lon });
    animate();
  }

  // ---------------- info
  function renderInfo() {
    document.title = "안내 · 골프날씨";
    const rel = S.rel, ks = ["icon", "blend", "ifs", "aifs", "ukmo", "gfs"];
    const tbl = rel ? '<div class="scroll"><table><thead><tr><th>모델</th>' + [1, 2, 3, 4, 5, 6, 7].map(d => "<th>" + d + "일</th>").join("") + "</tr></thead><tbody>" + ks.map(k => "<tr><td>" + MODELS.find(m => m[0] === k)[1] + "</td>" + [1, 2, 3, 4, 5, 6, 7].map(d => { const r = rel.models[k] && rel.models[k][d]; return "<td>" + (r ? r.score : "—") + "</td>"; }).join("") + "</tr>").join("") + "</tbody></table></div>" : "";
    app.innerHTML = '<p><a href="#/">← 처음으로</a></p><section class="card prose"><h1>안내</h1><p>골프날씨는 국내 골프장의 7일 예보를 여러 날씨 모델로 보여줍니다. 개인용으로 만든 개발 중 서비스입니다.</p>' +
      "<h2>모델</h2><ul>" + MODELS.map(([k]) => "<li><b>" + esc(FULL[k]) + "</b>" + ({ icon: " — 1~3일 예보에서 한국 검증 성적이 가장 좋았습니다. 7일차는 신뢰도가 크게 떨어집니다.", blend: " — 기온·바람은 평균, 강수량은 중앙값입니다. 기온은 1~7일 내내 상위권입니다.", ifs: " — 4일 이후에 강합니다.", aifs: " — AI 모델. 4~7일 기온·강수에서 가장 좋았습니다. 돌풍 자료가 없습니다.", ukmo: " — 1~3일 강수 적중이 좋습니다.", gfs: " — 참고용. 한국 검증 성적은 낮은 편입니다. 돌풍 자료가 없습니다.", kma: " — 예보관이 보정한 공식 예보. 약 4일까지 제공됩니다." }[k] || "") + "</li>").join("") + "</ul>" +
      "<h2>신뢰도 점수</h2><p>과거 예보를 기상청 관측과 대조한 결과입니다(" + (rel ? esc(rel.period) + ", " + esc(rel.note) : "") + "). 점수 = 강수 적중 45% + 기온 오차 35% + 풍속 오차 20%. 지금은 고정값이며, 지역별 자동 갱신은 다음 단계에서 넣습니다.</p>" + tbl +
      "<h2>날씨 지도</h2><p>강수(3시간 합계), 기온, 바람을 3시간 간격으로 7일까지 보여줍니다. ICON·ECMWF·GFS의 원래 격자를 0.1° 간격으로 다시 계산한 것이라, 골프장 상세 화면의 값(고도 보정)과 조금 다를 수 있습니다.</p><h2>읽는 법</h2><ul><li><b>라운드 강수</b>는 티업 시각부터 " + ROUND_H + "시간 동안의 합계입니다.</li><li><b>비 예보 모델 수</b>는 5개 모델(ICON, ECMWF, AIFS, UKMO, GFS) 중 라운드 시간에 " + WET_MM + " mm 이상을 예보한 수입니다.</li><li>기온은 골프장 고도에 맞춰 보정했습니다(100 m당 0.65°C).</li><li>원자료가 3·6시간 간격인 구간은 사이 시각을 보간했습니다.</li></ul>" +
      "<h2>자료</h2><ul><li>모델 예보: Open-Meteo 오픈데이터 (CC BY 4.0) — DWD, ECMWF, NOAA NCEP, UK Met Office</li><li>기상청 단기예보: 공공데이터포털</li><li>골프장: 행정안전부 인허가 데이터, © OpenStreetMap contributors (ODbL)</li><li>고도: Copernicus DEM</li></ul></section>";
  }

  // ---------------- events and routing
  app.addEventListener("click", ev => {
    const t = ev.target.closest("[data-model],[data-date],[data-step],#fav"); if (!t || !S.cur) return;
    if (t.dataset.model) { S.model = t.dataset.model; store.set("model", S.model); }
    else if (t.dataset.date) { S.date = t.dataset.date; S.hour = null; }
    else if (t.dataset.step) { S.step = +t.dataset.step; store.set("step", S.step); }
    else if (t.id === "fav") { let f = store.get("favs", []); f = f.includes(S.cur.id) ? f.filter(x => x !== S.cur.id) : [S.cur.id].concat(f); store.set("favs", f); }
    const y = window.scrollY; drawCourse(); window.scrollTo(0, y);
  });
  app.addEventListener("change", ev => { if (ev.target.id === "tee") { S.tee = +ev.target.value; store.set("tee", S.tee); S.hour = null; const y = window.scrollY; drawCourse(); window.scrollTo(0, y); } });
  function route() {
    const h = decodeURIComponent(location.hash || "");
    S.cur = null; window.scrollTo(0, 0); stopMap();
    if (h.startsWith("#c/")) renderCourse(h.slice(3)); else if (h === "#info") renderInfo(); else if (h.startsWith("#map")) renderMap(h.slice(5)); else renderHome();
  }
  (async function init() {
    try {
      const [c, m, r] = await Promise.all([fetch("data/courses.json").then(x => x.json()), fetch("data/meta.json").then(x => x.json()), fetch("reliability.json").then(x => x.json()).catch(() => null)]);
      S.courses = c.courses; S.byId = new Map(c.courses.map(x => [x.id, x])); S.meta = m; S.rel = r;
      $("#foot-meta").textContent = "골프장 " + c.courses.length + "곳 · 예보 생성 " + m.generated.replace("T", " ").slice(0, 16) + " (한국 시간) · 약 3시간마다 갱신";
    } catch (e) { app.innerHTML = "<p>예보 자료를 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요.</p>"; return; }
    window.addEventListener("hashchange", route); route();
  })();
})();
