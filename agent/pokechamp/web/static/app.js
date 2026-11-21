// 포챔 보조 AI 웹 화면. 빌드 도구 없이 쓰는 바닐라 JS.
"use strict";

const TYPE_KO = {
  Normal: "노말", Fire: "불꽃", Water: "물", Electric: "전기", Grass: "풀", Ice: "얼음", Fighting: "격투",
  Poison: "독", Ground: "땅", Flying: "비행", Psychic: "에스퍼", Bug: "벌레", Rock: "바위", Ghost: "고스트",
  Dragon: "드래곤", Dark: "악", Steel: "강철", Fairy: "페어리",
};
const TYPE_COLOR = {
  Normal: "#8f9294", Fire: "#e62829", Water: "#2980ef", Electric: "#d9a800", Grass: "#3fa129", Ice: "#2bb7da",
  Fighting: "#ff8000", Poison: "#9141cb", Ground: "#915121", Flying: "#5f9fe0", Psychic: "#ef4179", Bug: "#879512",
  Rock: "#a49a6c", Ghost: "#704170", Dragon: "#5060e1", Dark: "#624d4e", Steel: "#4f93ab", Fairy: "#e05be0",
};
const CATEGORY_KO = { Physical: "물리", Special: "특수", Status: "변화" };
// 능력치는 어디서든 H·A·B·C·D·S 순서와 글자로 보인다 (데이터의 키 순서와 상관없이)
const STAT_ORDER = ["hp", "atk", "def", "spa", "spd", "spe"];
const STAT_KO = { hp: "H", atk: "A", def: "B", spa: "C", spd: "D", spe: "S" };
const statEntries = (d) => STAT_ORDER.filter((k) => d && k in d).map((k) => [k, d[k]]);
const spText = (d) => statEntries(d).filter(([, v]) => v).map(([k, v]) => `${STAT_KO[k]}${v}`).join(" ");
const KST = { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false };

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ ok: false, error: `응답을 읽지 못했습니다 (${res.status})` }));
  if (!data.ok) throw new Error(data.error || "알 수 없는 오류");
  return data.result;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 4000);
}

async function withBusy(btn, fn) {
  btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message); } finally { btn.disabled = false; }
}

const typeChip = (t) => `<span class="type" style="--tc:${TYPE_COLOR[t] || "#888"}">${TYPE_KO[t] || esc(t)}</span>`;
const typeChips = (ts) => `<span class="types">${(ts || []).map(typeChip).join("")}</span>`;
const fmtTs = (ts) => (ts ? new Date(ts * 1000).toLocaleString("ko-KR", KST) : "-");

function sprite(id, cls = "") {
  const base = id.split("-")[0];
  return `<img class="${cls}" alt="" loading="lazy" src="https://play.pokemonshowdown.com/sprites/gen5/${esc(id)}.png"
    onerror="if(!this.dataset.f){this.dataset.f=1;this.src='https://play.pokemonshowdown.com/sprites/gen5/${esc(base)}.png'}else{this.style.visibility='hidden'}">`;
}

// ---------------- 탭 ----------------
const loaded = {};
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab-panel").forEach((p) => { p.hidden = p.id !== `tab-${name}`; });
  history.replaceState(null, "", `#${name}`);
  if (!loaded[name]) { loaded[name] = true; ({ progress: loadProgress, dex: loadDex, news: loadNews })[name]?.(); }
  // 탭을 열 때마다 (클릭이든 새로고침으로 주소 #탭에서 바로 열리든) 목록을 새로 불러온다
  ({
    team: () => { loadTeams(); if (!builder.options) renderBuilder(); },
    live: loadLiveTeams,
    calc: loadCalc,
    review: loadBattles,
  })[name]?.();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// 엔진 상태: 평소에는 보이지 않고, 꺼져 있을 때만 상단에 안내한다 (자세한 상태는 개발 현황 탭)
async function loadEngine() {
  const pill = $("#engine-pill");
  let up = false;
  try { up = (await api("/api/engine")).up; } catch { up = false; }
  pill.textContent = up ? "엔진 연결됨" : "엔진 꺼짐";
  pill.className = `pill ${up ? "up" : "down"}`;
  $("#engine-alert").hidden = up;
  return up;
}
$("#engine-retry").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  toast((await loadEngine()) ? "엔진에 연결했습니다." : "아직 엔진에 연결되지 않았습니다.");
}));

// ---------------- 진행 현황 ----------------
let progress = null;
let selectedStage = null;

async function loadProgress() {
  try {
    progress = await api("/api/progress");
  } catch (e) { toast(e.message); return; }
  const g = progress.git;
  $("#branch-pill").textContent = `브랜치 ${g.branch}`;
  const cur = progress.stages.find((s) => s.status === "in_progress") || progress.stages[0];
  selectedStage = selectedStage ?? cur.id;
  renderTrack();
  renderStage();
  $("#git-log").innerHTML = g.log.map((c) =>
    `<li><code>${esc(c.hash)}</code><span class="muted">${esc(c.date)}</span><span>${esc(c.subject)}</span></li>`).join("");
  $("#git-dirty").textContent = g.uncommitted ? `커밋 안 된 변경 ${g.uncommitted}개` : "작업 트리 깨끗함";
  $("#verified-table tbody").innerHTML = progress.verified.map((r) => {
    const ok = r.state.includes("✅");
    const warn = r.state.includes("⏳") || r.state.includes("⚠️");
    return `<tr><td>${r.no}</td><td>${esc(r.item)}</td><td>${mdInline(r.result)}</td>
      <td><span class="state-badge ${ok ? "ok" : warn ? "warn" : ""}">${esc(r.state)}</span></td></tr>`;
  }).join("");
  loadStatus();
}

function mdInline(s) {
  return esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>");
}

function renderTrack() {
  $("#stage-track").innerHTML = progress.stages.map((s) => {
    const total = s.items.length;
    const done = s.items.filter((i) => i.done).length;
    const fill = total ? Math.round((done / total) * 100) : 0;
    const label = { done: "완료", in_progress: "진행 중", todo: "예정" }[s.status];
    return `<li><button class="stage ${s.status} ${s.id === selectedStage ? "selected" : ""}" data-id="${s.id}"
      style="--fill:${fill}%" aria-label="단계 ${s.id} ${esc(s.title)} (${label})">
      <span class="stage-bar"></span><span class="stage-num">${s.id}</span>
      <span class="stage-title">${esc(s.title)}</span></button></li>`;
  }).join("");
  document.querySelectorAll(".stage").forEach((b) => b.addEventListener("click", () => {
    selectedStage = Number(b.dataset.id); renderTrack(); renderStage();
  }));
}

function renderStage() {
  const s = progress.stages.find((x) => x.id === selectedStage);
  const label = { done: "완료", in_progress: "진행 중", todo: "예정" }[s.status];
  const done = s.items.filter((i) => i.done).length;
  $("#stage-detail").innerHTML = `
    <div class="panel-head"><h2>단계 ${s.id}. ${esc(s.title)}</h2><span class="state-badge ${s.status === "done" ? "ok" : s.status === "in_progress" ? "warn" : ""}">${label}</span></div>
    ${s.items.length ? `<p class="muted">작업 ${done}/${s.items.length}개 완료 (${esc(progress.updated_at)} 기준)</p>
      <ul class="checklist">${s.items.map((i) => `<li class="${i.done ? "done" : ""}"><span class="box" aria-hidden="true"></span><span>${esc(i.text)}${i.done ? "" : ""}</span></li>`).join("")}</ul>`
      : `<p class="muted">아직 작업 목록을 만들지 않았습니다. 앞 단계가 끝나면 채워집니다.</p>`}
    <div class="criteria"><strong>완료 기준</strong> ${esc(s.criteria)}</div>`;
}

function statsLines(stats) {
  if (!stats || !stats.length) {
    return `<span class="k">통계 스냅샷</span><span>없음. 배틀 전에 터미널에서 <code>uv run pokechamp update-stats</code>를 실행하세요.</span>`;
  }
  return stats.map((x) => `<span class="k">통계 (${esc(x.source)})</span><span>데이터 ${esc(x.latest_date)}
    (${x.age_days === 0 ? "오늘" : `${x.age_days}일 전`}), 시즌 ${esc(x.official_season)}, 스냅샷 ${x.snapshots}개
    ${x.verified ? "" : `<br><span class="muted">출처는 ${esc(x.page_season)}로 표기, 날짜 기준 공식 시즌으로 판정</span>`}
    ${x.age_days >= 2 ? `<br><span class="state-badge warn">오래됨, update-stats 권장</span>` : ""}</span>`).join("");
}

async function loadStatus() {
  try {
    const st = await api("/api/status");
    const s = st.static;
    $("#status-box").innerHTML = s ? `
      <span class="k">정적 데이터</span><span>버전 ${s.version}, 레귤레이션 ${esc(s.regulation)}${s.regulation_ended ? " (끝남, 갱신 필요)" : ""}</span>
      <span class="k">레귤레이션 기간</span><span>${fmtTs(s.regulation_starts_at)} ~ ${fmtTs(s.regulation_ends_at)}</span>
      <span class="k">수록</span><span>포켓몬 ${s.counts.species} (메가 ${s.counts.mega_forms}), 기술 ${s.counts.moves}, 도구 ${s.counts.items}, 특성 ${s.counts.abilities}</span>
      <span class="k">Showdown</span><span><code>${esc(s.showdown_commit.slice(0, 7))}</code></span>
      ${statsLines(st.stats)}`
      : `<span class="k">정적 데이터</span><span>없음. 터미널에서 <code>uv run pokechamp update-static</code>을 실행하세요.</span>`;
  } catch (e) { $("#status-box").textContent = e.message; }
}

$("#run-tests").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  $("#tests-result").textContent = "실행 중… (10초 정도 걸립니다)";
  const r = await api("/api/tests/run", {});
  const line = (name, x) => `${name} ${x.ok ? "통과" : "실패"} (${x.summary})`;
  $("#tests-result").textContent = `${line("Python", r.python)} · ${line("엔진", r.engine)}`;
  const tail = $("#tests-tail");
  tail.hidden = r.python.ok && r.engine.ok;
  tail.textContent = [r.python.ok ? "" : r.python.tail, r.engine.ok ? "" : r.engine.tail].join("\n");
}));

$("#run-selfplay").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  $("#selfplay-result").textContent = "실행 중…";
  const r = await api("/api/selfplay", { n: 100 });
  $("#selfplay-result").textContent =
    `${r.finished}/${r.games}판 완료, 오류 ${r.errors}건, 선공측 ${r.p1_wins}승 ${r.p2_wins}패, 턴 중앙값 ${r.turns_median}, ${r.seconds}초 (시드 ${r.seed})`;
}));

function hpClass(pct) { return pct > 50 ? "" : pct > 20 ? "mid" : "lo"; }

// ---------------- 도감 ----------------
let dex = [];
async function loadDex() {
  try {
    const r = await api("/api/pokemon");
    dex = r.pokemon;
  } catch (e) { toast(e.message); return; }
  const types = [...new Set(dex.flatMap((p) => p.types))].sort((a, b) => (TYPE_KO[a] || a).localeCompare(TYPE_KO[b] || b, "ko"));
  $("#dex-type").innerHTML = `<option value="">모든 타입</option>` + types.map((t) => `<option value="${t}">${TYPE_KO[t] || t}</option>`).join("");
  renderDex();
}
["#dex-q", "#dex-type", "#dex-mega"].forEach((s) => $(s).addEventListener("input", renderDex));

function renderDex() {
  const q = $("#dex-q").value.trim().toLowerCase();
  const t = $("#dex-type").value;
  const megaOnly = $("#dex-mega").checked;
  const rows = dex.filter((p) => (!q || p.name_ko.toLowerCase().includes(q) || p.name_en.toLowerCase().includes(q))
    && (!t || p.types.includes(t)) && (!megaOnly || p.is_mega));
  $("#dex-count").textContent = `${rows.length}마리`;
  $("#dex-list").innerHTML = rows.map((p) => {
    const bst = Object.values(p.stats).reduce((a, b) => a + b, 0);
    return `<button class="dex-row" role="listitem" data-id="${p.id}">
      <span class="no">${String(p.num).padStart(4, "0")}</span>${sprite(p.sprite)}
      <span><strong>${esc(p.name_ko)}</strong> <span class="muted">${esc(p.name_en)}</span><br>${typeChips(p.types)}</span>
      <span class="bst">합계 ${bst}</span></button>`;
  }).join("");
  document.querySelectorAll(".dex-row").forEach((r) => r.addEventListener("click", () => showDex(r.dataset.id)));
}

async function showDex(id) {
  document.querySelectorAll(".dex-row").forEach((r) => r.setAttribute("aria-current", String(r.dataset.id === id)));
  const p = dex.find((x) => x.id === id);
  const box = $("#dex-detail");
  box.innerHTML = `<p class="muted">불러오는 중…</p>`;
  try {
    const d = await api(`/api/pokemon?id=${encodeURIComponent(id)}`);
    const max = 200;
    const megas = p.megas.map((m) => dex.find((x) => x.id === m)).filter(Boolean);
    box.innerHTML = `
      <div class="active-card" style="border:0;padding:0">${sprite(p.sprite)}<div>
        <div class="nm">${esc(p.name_ko)}</div><div class="muted">${esc(p.name_en)}${p.official_code ? `, 공식 코드 ${esc(p.official_code)}` : ", 메가 폼"}</div>
        ${typeChips(p.types)}<div class="info-line">특성 ${p.abilities.map(esc).join(", ")}</div></div></div>
      <div class="statbars">${statEntries(p.stats).map(([k, v]) => `<span class="muted">${STAT_KO[k]}</span><strong>${v}</strong>
        <span class="statbar"><i style="width:${Math.min(100, (v / max) * 100)}%"></i></span>`).join("")}</div>
      ${megas.length ? `<p class="info-line">메가진화: ${megas.map((m) => `<a href="#" data-id="${m.id}">${esc(m.name_ko)}</a>`).join(", ")}</p>` : ""}
      ${d.sets && d.sets.length ? `<h3 style="margin-top:14px">자주 보이는 세트 (원형 상위 ${d.sets.length}개)</h3>
      <table class="table"><thead><tr><th>확률</th><th>도구</th><th>기술</th><th>성격·배분</th></tr></thead><tbody>
      ${d.sets.map((x) => `<tr><td>${(x.weight * 100).toFixed(1)}%</td><td>${esc(x.item || "-")}${x.mega ? " (메가)" : ""}</td>
        <td>${x.moves.map(esc).join(", ")}</td><td class="muted">${esc(x.nature || "")}${x.spread ? ` ${spText(x.spread)}` : ""}</td></tr>`).join("")}
      </tbody></table><p class="muted">${d.sets.some((x) => !x.rule_generated) ? "완성 세트 근거 포함." : "통계의 주변 분포에 규칙 필터와 보정(IPF)을 적용해 만든 추정 세트입니다."}</p>` : ""}
      <h3 style="margin-top:14px">배울 수 있는 기술 ${d.moves.length}개</h3>
      <table class="table"><thead><tr><th>기술</th><th>타입</th><th>분류</th><th>위력</th><th>명중</th><th>PP</th></tr></thead>
      <tbody>${d.moves.map((m) => `<tr title="${esc(m.description)}"><td>${esc(m.name_ko || m.name_en)}</td><td>${typeChip(m.type)}</td>
        <td>${CATEGORY_KO[m.category]}</td><td>${m.power || "-"}</td><td>${m.accuracy ?? "-"}</td><td>${m.max_pp}</td></tr>`).join("")}</tbody></table>`;
    box.querySelectorAll("a[data-id]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); showDex(a.dataset.id); }));
  } catch (e) { box.innerHTML = `<p>${esc(e.message)}</p>`; }
}

// ---------------- 내 팀 ----------------
$("#team-random").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const r = await api("/api/team/random", {});
  $("#team-text").value = r.text.trim();
}));
// ---------------- 검색 가능한 선택 상자 ----------------
// options: [{id, name, name_en?, note?, sprite?, popular?}] — popular가 참이면 위에 '많이 씀'으로 먼저 보인다
function pickerBtn(attrs, label, placeholder) {
  return `<button type="button" class="picker-btn" ${attrs}>${label ? esc(label) : `<span class="muted">${esc(placeholder || "선택")}</span>`}<span class="caret">▾</span></button>`;
}
function closePicker() {
  document.querySelectorAll(".picker-pop").forEach((p) => p.remove());
  document.removeEventListener("mousedown", pickerOutside, true);
}
function pickerOutside(e) {
  const pop = document.querySelector(".picker-pop");
  if (pop && !pop.contains(e.target) && !e.target.closest(".picker-btn")) closePicker();
}
function openPicker(btn, options, onPick, { allowClear = true } = {}) {
  closePicker();
  const pop = document.createElement("div");
  pop.className = "picker-pop";
  pop.innerHTML = `<input class="input picker-search" placeholder="검색 (한국어·영어)"><div class="picker-list"></div>`;
  document.body.appendChild(pop);
  const r = btn.getBoundingClientRect();
  const width = Math.min(320, document.documentElement.clientWidth - 16);
  pop.style.width = width + "px";
  pop.style.top = window.scrollY + r.bottom + 4 + "px";
  pop.style.left = Math.max(8, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - width - 8)) + "px";
  const list = pop.querySelector(".picker-list"), search = pop.querySelector(".picker-search");
  const norm = (x) => String(x || "").toLowerCase().replace(/[\s\-·()']/g, "");
  const render = () => {
    const q = norm(search.value);
    const hit = (o) => !q || norm(o.name).includes(q) || norm(o.name_en).includes(q) || norm(o.id).includes(q);
    const pop1 = options.filter((o) => o.popular && hit(o)), rest = options.filter((o) => !o.popular && hit(o));
    const row = (o) => `<div class="picker-item" data-id="${esc(o.id)}">${o.sprite ? sprite(o.sprite) : ""}<span>${esc(o.name)}</span>${o.note ? `<span class="muted small">${esc(o.note)}</span>` : ""}</div>`;
    list.innerHTML = (allowClear && !q ? `<div class="picker-item clear" data-id="">(비우기)</div>` : "")
      + (pop1.length ? `<div class="picker-group">많이 씀</div>${pop1.map(row).join("")}` : "")
      + (rest.length ? `${pop1.length ? `<div class="picker-group">전체</div>` : ""}${rest.slice(0, 300).map(row).join("")}` : "")
      + (!pop1.length && !rest.length ? `<div class="muted picker-empty">찾는 항목이 없습니다</div>` : "");
    list.querySelectorAll(".picker-item").forEach((el) => el.addEventListener("click", () => {
      const o = options.find((x) => x.id === el.dataset.id) || { id: "", name: "" };
      closePicker(); onPick(o);
    }));
  };
  search.addEventListener("input", render);
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closePicker();
    if (e.key === "Enter") { const first = list.querySelector(".picker-item:not(.clear)"); if (first) first.click(); }
  });
  render(); search.focus();
  setTimeout(() => document.addEventListener("mousedown", pickerOutside, true), 0);
}
const withPopular = (all, popularIds) => {
  const order = new Map((popularIds || []).map((id, i) => [id, i]));
  return all.map((o) => ({ ...o, popular: order.has(o.id), _o: order.get(o.id) ?? 999 })).sort((a, b) => a._o - b._o);
};

// ---------------- 팀 만들기 (선택 방식) ----------------
const STAT_KEYS = STAT_ORDER.map((k) => [k, STAT_KO[k]]);
const builder = { options: null, speciesData: {}, slots: Array.from({ length: 6 }, () => emptySlot()) };
function emptySlot() { return { species: "", item: "", ability: "", nature: "", evs: {}, moves: ["", "", "", ""] }; }
async function builderOptions() {
  if (!builder.options) builder.options = await api("/api/builder/options");
  return builder.options;
}
async function speciesData(id) {
  if (!id) return null;
  if (!builder.speciesData[id]) builder.speciesData[id] = await api(`/api/builder/species?id=${encodeURIComponent(id)}`);
  return builder.speciesData[id];
}
const nameOf = (list, id) => (list.find((x) => x.id === id) || {}).name || "";
async function renderBuilder() {
  const o = await builderOptions();
  const cards = await Promise.all(builder.slots.map(async (sl, i) => {
    const sd = await speciesData(sl.species);
    const sp = o.species.find((x) => x.id === sl.species);
    const total = STAT_KEYS.reduce((t, [k]) => t + (Number(sl.evs[k]) || 0), 0);
    const over = total > 66 || STAT_KEYS.some(([k]) => (Number(sl.evs[k]) || 0) > 32);
    return `<div class="slot-card">
      <div class="slot-head">${sp ? sprite(sp.sprite) : `<span class="slot-num">${i + 1}</span>`}
        ${pickerBtn(`data-bpick="species" data-slot="${i}"`, sp && sp.name, "포켓몬 고르기")}</div>
      ${sl.species ? `
      <div class="slot-row"><span class="lbl">도구</span>${pickerBtn(`data-bpick="item" data-slot="${i}"`, nameOf(o.items, sl.item), "도구")}</div>
      <div class="slot-row"><span class="lbl">특성</span>${pickerBtn(`data-bpick="ability" data-slot="${i}"`, nameOf(sd ? sd.abilities : [], sl.ability), "특성")}</div>
      <div class="slot-row"><span class="lbl">성격</span>${pickerBtn(`data-bpick="nature" data-slot="${i}"`, nameOf(o.natures, sl.nature) && `${nameOf(o.natures, sl.nature)} (${(o.natures.find((x) => x.id === sl.nature) || {}).note})`, "성격")}</div>
      <div class="slot-moves">${[0, 1, 2, 3].map((k) => pickerBtn(`data-bpick="move" data-slot="${i}" data-k="${k}"`, nameOf(sd ? sd.moves : [], sl.moves[k]), `기술 ${k + 1}`)).join("")}</div>
      <div class="slot-sp">${STAT_KEYS.map(([k, n]) => `<label>${n}<input type="number" min="0" max="32" class="input" data-sp="${k}" data-slot="${i}" value="${sl.evs[k] || ""}"></label>`).join("")}</div>
      <div class="slot-foot"><span class="${over ? "err" : "muted"} small">SP 합계 ${total}/66${over ? " · 능력치당 32, 합계 66을 넘었습니다" : ""}</span>
        <span><button class="btn btn-sm" data-bfill="${i}">많이 쓰는 세트로 채우기</button> <button class="btn btn-sm" data-bclear="${i}">비우기</button></span></div>` : ""}
    </div>`;
  }));
  $("#builder").innerHTML = cards.join("");
  bindBuilder();
}
function bindBuilder() {
  const o = builder.options;
  document.querySelectorAll("[data-bpick]").forEach((btn) => btn.addEventListener("click", async () => {
    const i = Number(btn.dataset.slot), sl = builder.slots[i], kind = btn.dataset.bpick;
    const sd = await speciesData(sl.species);
    if (kind === "species") {
      openPicker(btn, o.species.map((x) => ({ ...x, note: (x.types || []).join("/") })), async (p) => {
        builder.slots[i] = { ...emptySlot(), species: p.id };
        if (p.id) {
          const d = await speciesData(p.id);
          if (d.abilities.length === 1 || (d.popular.abilities || []).length) builder.slots[i].ability = d.popular.abilities[0] || d.abilities[0].id;
        }
        renderBuilder();
      });
    } else if (kind === "item") {
      openPicker(btn, withPopular(o.items.map((x) => ({ ...x, note: x.mega ? "메가스톤" : x.berry ? "열매" : "" })), sd && sd.popular.items), (p) => { sl.item = p.id; renderBuilder(); });
    } else if (kind === "ability") {
      openPicker(btn, withPopular(sd.abilities, sd.popular.abilities), (p) => { sl.ability = p.id; renderBuilder(); });
    } else if (kind === "nature") {
      openPicker(btn, withPopular(o.natures, sd && sd.popular.natures), (p) => { sl.nature = p.id; renderBuilder(); });
    } else if (kind === "move") {
      const k = Number(btn.dataset.k);
      const taken = sl.moves.filter((m, j) => m && j !== k);
      const opts = sd.moves.filter((m) => !taken.includes(m.id)).map((m) => ({ ...m, note: `${m.type} · ${m.category === "Status" ? "변화" : m.category === "Physical" ? "물리" : "특수"}${m.power ? " " + m.power : ""}` }));
      openPicker(btn, withPopular(opts, sd.popular.moves), (p) => { sl.moves[k] = p.id; renderBuilder(); });
    }
  }));
  document.querySelectorAll("[data-sp]").forEach((inp) => inp.addEventListener("change", () => {
    const sl = builder.slots[Number(inp.dataset.slot)];
    const v = Math.max(0, Math.min(32, Number(inp.value) || 0));
    if (v) sl.evs[inp.dataset.sp] = v; else delete sl.evs[inp.dataset.sp];
    renderBuilder();
  }));
  document.querySelectorAll("[data-bclear]").forEach((b) => b.addEventListener("click", () => { builder.slots[Number(b.dataset.bclear)] = emptySlot(); renderBuilder(); }));
  document.querySelectorAll("[data-bfill]").forEach((b) => b.addEventListener("click", async () => {
    const i = Number(b.dataset.bfill), sl = builder.slots[i], d = await speciesData(sl.species), p = d.popular;
    const usedItems = builder.slots.filter((x, j) => j !== i).map((x) => x.item);
    sl.item = (p.items || []).find((x) => !usedItems.includes(x)) || sl.item;
    sl.ability = (p.abilities || [])[0] || sl.ability;
    sl.nature = (p.natures || [])[0] || sl.nature;
    if ((p.spreads || [])[0]) sl.evs = Object.fromEntries(Object.entries(p.spreads[0]).filter(([, v]) => v));
    const learn = new Set(d.moves.map((m) => m.id));
    sl.moves = [...(p.moves || []).filter((m) => learn.has(m)).slice(0, 4), "", "", "", ""].slice(0, 4);
    renderBuilder();
  }));
}
const builderSets = () => builder.slots.filter((s) => s.species).map((s) => ({ ...s, moves: s.moves.filter(Boolean) }));
function builderResult(r) {
  $("#builder-result").innerHTML = `<div class="panel ${r.valid ? "" : "warn-box"}">
    <p>${r.name ? `'${esc(r.name)}' 저장함 · ` : ""}${r.valid === null ? "엔진 검증을 건너뛰었습니다" : r.valid ? "규칙 통과" : "규칙 위반 경고 (팀은 그대로 쓸 수 있습니다)"}</p>
    ${(r.problems || []).map((p) => `<p class="small">${esc(p)}</p>`).join("")}${(r.notes || []).map((n) => `<p class="small muted">${esc(n)}</p>`).join("")}</div>`;
}
$("#builder-check").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  builderResult(await api("/api/team/save_sets", { name: "검증", sets: builderSets(), check_only: true }));
}));
$("#builder-save").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const name = $("#builder-name").value.trim();
  if (!name) { toast("팀 이름을 넣으세요."); return; }
  builderResult(await api("/api/team/save_sets", { name, sets: builderSets(), replace: $("#builder-replace").checked }));
  loadTeams();
}));
$("#builder-clear").addEventListener("click", () => { builder.slots = Array.from({ length: 6 }, () => emptySlot()); $("#builder-name").value = ""; renderBuilder(); });
function loadIntoBuilder(sets, name) {
  builder.slots = Array.from({ length: 6 }, (_, i) => {
    const s = sets[i];
    return s ? { species: s.species, item: s.item || "", ability: s.ability || "", nature: s.nature || "", evs: { ...(s.evs || {}) },
      moves: [...(s.moves || []), "", "", "", ""].slice(0, 4) } : emptySlot();
  });
  if (name !== undefined) { $("#builder-name").value = name; $("#builder-replace").checked = true; }
  renderBuilder();
  document.getElementById("builder").scrollIntoView({ behavior: "smooth" });
}
$("#team-to-builder").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const r = await api("/api/team/parse_ids", { text: $("#team-text").value });
  loadIntoBuilder(r.sets, $("#team-name").value.trim() || undefined);
}));

function teamResultHtml(r, saved) {
  const names = r.team.map((x) => esc(x.species) + (x.filled ? " (기본값 채움)" : "")).join(", ");
  const state = r.valid === null ? `<span class="state-badge">검증 건너뜀</span>`
    : r.valid ? `<span class="state-badge ok">규칙 통과</span>` : `<span class="state-badge warn">위반 ${r.problems.length}개</span>`;
  return `<p>${saved ? `<strong>'${esc(saved)}' 저장함</strong> ` : ""}${state}</p>
    ${r.problems.length ? `<ul>${r.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
    ${r.missing && r.missing.length ? `<p class="info-line">빈 항목: ${r.missing.map((m) => `${esc(m.species)} ${esc(m.fields.join(", "))}`).join(" / ")}</p>` : ""}
    ${r.notes && r.notes.length ? `<p class="info-line">채운 값: ${r.notes.map(esc).join("<br>")}</p>` : ""}
    <p class="info-line">${names}</p>`;
}
$("#team-validate").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const text = $("#team-text").value.trim();
  if (!text) { toast("검증할 팀을 붙여 넣으세요."); return; }
  const r = await api("/api/team/validate", { text, fill: $("#team-fill").checked });
  $("#team-result").innerHTML = teamResultHtml(r);
}));
$("#team-save").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const text = $("#team-text").value.trim();
  const name = $("#team-name").value.trim();
  if (!text || !name) { toast("팀 텍스트와 이름을 넣으세요."); return; }
  const r = await api("/api/team/save", { text, name, fill: $("#team-fill").checked, keep_empty: $("#team-keep").checked,
    replace: $("#team-replace").checked });
  $("#team-result").innerHTML = teamResultHtml(r, r.name);
  loadTeams();
}));
async function loadTeams() {
  try {
    const teams = await api("/api/teams");
    $("#team-list").innerHTML = teams.length ? teams.map((t) => `<li class="team-row">
      <div><strong>${esc(t.name)}</strong> <span class="muted">${t.source === "manual" ? "직접 입력" : "추천"}
        ${t.valid === 0 ? ` · ⚠️ 경고 ${t.problems.length}개` : ""}</span>
        <div class="pick-mons">${t.team.map((x) => sprite(spriteOf(x.species))).join("")}</div></div>
      <div class="team-actions"><button class="btn btn-sm" data-tshow="${esc(t.name)}">보기</button>
        <button class="btn btn-sm" data-tload="${esc(t.name)}">편집</button>
        <button class="btn btn-sm" data-tdel="${esc(t.name)}">삭제</button></div>
      <div class="team-detail" id="tdetail-${esc(t.name)}"></div></li>`).join("") : `<li class="muted">아직 없습니다. 왼쪽에 팀을 붙여 넣고 이름을 정해 저장하세요.</li>`;
    document.querySelectorAll("[data-tshow]").forEach((b) => b.addEventListener("click", async () => {
      const box = document.getElementById(`tdetail-${b.dataset.tshow}`);
      if (box.innerHTML) { box.innerHTML = ""; return; }
      const d = await api("/api/team/show", { name: b.dataset.tshow });
      box.innerHTML = `<ul class="plain small">${d.members.map((m) => `<li>${esc(m.name)}${m.item ? ` @ ${esc(m.item)}` : ""}
        ${m.filled.length ? ` <span class="muted">(통계 기본값으로 채움: ${esc(m.filled.join(", "))})</span>` : ""}</li>`).join("")}</ul>
        ${d.problems.length ? `<div class="warn-box">${d.problems.map((p) => `<p>${esc(p)}</p>`).join("")}</div>` : ""}
        <pre class="tail">${esc(d.export)}</pre>`;
    }));
    document.querySelectorAll("[data-tload]").forEach((b) => b.addEventListener("click", async () => {
      const d = await api("/api/team/show", { name: b.dataset.tload });
      loadIntoBuilder(d.sets, d.name);
      toast("팀 만들기 칸에 불러왔습니다. 고친 뒤 '팀 저장'을 누르면 덮어씁니다.");
    }));
    document.querySelectorAll("[data-tdel]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm(`'${b.dataset.tdel}' 팀을 지울까요? 되돌릴 수 없습니다.`)) return;
      await api("/api/team/delete", { name: b.dataset.tdel });
      toast("지웠습니다."); loadTeams();
    }));
  } catch (e) { $("#team-list").textContent = e.message; }
}
function spriteOf(name) {
  const base = String(name).split("-")[0], forme = String(name).split("-").slice(1).join("");
  const clean = (x) => x.toLowerCase().replace(/[^a-z0-9]/g, "");
  return clean(base) + (forme ? "-" + clean(forme) : "");
}

// ---------------- 파티 추천 ----------------
const pct1 = (x) => `${(x * 100).toFixed(1)}%`;
function fmtSim(r) {
  return r && r.win_rate !== null ? `${pct1(r.win_rate)} <span class="muted">(95% 구간 ${pct1(r.lo)}~${pct1(r.hi)}, ${r.games.toLocaleString()}판)</span>` : "결과 없음";
}
function recHtml(r) {
  const s = r.score;
  const members = r.members.map((m) => `<li class="rec-mon">
      ${sprite(m.sprite)}
      <div><div class="nm">${esc(m.species_ko)}${m.mega ? ` <span class="state-badge">메가: ${esc(m.mega)}</span>` : ""}
        <span class="muted">균형 선출 ${Math.round(m.pick_rate * 100)}%</span></div>
        <div class="info-line">${esc(m.line_ko)}</div>
        <div class="muted">${esc(m.role)}</div></div></li>`).join("");
  const megas = r.mega.holders.map((h) => `<li><strong>${esc(h.mega)}</strong> <span class="muted">선출 ${Math.round(h.pick_rate * 100)}%</span>
      · 약한 상대: ${esc(h.weak_to.join(", ") || "없음")}
      <ul>${h.partners.map((p) => `<li>${esc(p.pair.join(" + "))}: 약한 상대의 ${Math.round(p.covers * 100)}% 커버</li>`).join("")}</ul></li>`).join("");
  const v = r.validation;
  const diff = (d) => d && d.diff !== null ? `${d.diff >= 0 ? "+" : ""}${pct1(d.diff)} (95% 구간 ${pct1(d.lo)}~${pct1(d.hi)})` : "-";
  return `
    ${r.warnings.length ? `<div class="panel warn-box">${r.warnings.map((w) => `<p>${esc(w)}</p>`).join("")}</div>` : ""}
    <div class="grid-2">
      <div class="panel">
        <div class="panel-head"><h2>추천 팀</h2><span class="muted">시드 ${esc(r.seed)} · ${esc(r.stats)}${r.saved_as ? ` · '${esc(r.saved_as)}' 저장함` : ""}</span></div>
        <ul class="rec-list">${members}</ul>
      </div>
      <div class="stack">
        <div class="panel">
          <div class="panel-head"><h2>팀 점수 ${s.score.toFixed(3)}</h2></div>
          <div class="kv">
            <span class="k">기대값</span><span>${s.mean.toFixed(3)}</span>
            <span class="k">하위 ${Math.round(s.cvar_fraction * 100)}% 평균</span><span>${s.cvar.toFixed(3)}</span>
            <span class="k">시나리오 편차</span><span>${s.sd.toFixed(3)}</span>
            ${r.scenarios.map((x) => `<span class="k">${esc(x.name)}</span><span>${x.expected.toFixed(3)}</span>`).join("")}
          </div>
          <p class="muted">점수 = 기대값 − ${s.alpha}×(기대값 − 하위 평균) − ${s.beta}×편차. 빠른 상성 근사로 구한 선출 게임 값이라 실제 승률이 아니라 팀끼리 비교하는 점수입니다.</p>
        </div>
        ${v ? `<div class="panel"><div class="panel-head"><h2>시뮬레이션 검증</h2></div>
          <div class="kv">
            <span class="k">추천 팀</span><span>${fmtSim(v.recommended)}</span>
            <span class="k">사용률 상위 팀</span><span>${fmtSim(v.usage_top)}</span>
            <span class="k">무작위 팀</span><span>${fmtSim(v.random)}</span>
            <span class="k">상위 팀 대비</span><span>${diff(v.vs_usage_top)}</span>
            <span class="k">무작위 대비</span><span>${diff(v.vs_random)}</span>
          </div>
          <p class="muted">${esc(v.policy)}. 사용률 상위 팀: ${esc(v.usage_top_team.join(", "))}</p></div>` : ""}
        ${r.rerank ? `<div class="panel"><div class="panel-head"><h2>3:3 시뮬레이션 재평가</h2></div>
          <ul class="plain">${r.rerank.candidates.map((c) => `<li>${c.chosen ? "<strong>" : ""}후보 ${c.rank}: 승률 ${pct1(c.win_rate)}${c.chosen ? " (선택)</strong>" : ""}
            <span class="muted">점수 ${c.score.toFixed(3)} · ${esc(c.members.join(", "))}</span></li>`).join("")}</ul>
          ${r.rerank.local ? `<p class="muted">국소 탐색 뒤 ${pct1(r.rerank.local.win_rate)} (전 ${pct1(r.rerank.local.before)}) → ${r.rerank.local.kept ? "유지" : "유의하게 낮아져 되돌림"}</p>` : ""}
          <p class="muted">${esc(r.rerank.policy)}</p></div>` : ""}
        ${megas ? `<div class="panel"><div class="panel-head"><h2>메가 후보 점검</h2></div><ul class="plain">${megas}</ul></div>` : ""}
        <div class="panel"><div class="panel-head"><h2>남는 약점</h2></div>
          ${r.weaknesses.length ? `<ul class="plain">${r.weaknesses.map((w) => `<li>${esc(w.species)} <span class="muted">가중치 ${pct1(w.weight)} · 최선 ${esc(w.best_answer)} ${w.best_score.toFixed(2)}</span></li>`).join("")}</ul>`
            : `<p class="muted">출현 상위 위협 중 팀에서 가장 나은 답도 불리한 상대는 없습니다.</p>`}
          ${r.replacements.length ? `<p><strong>교체 후보</strong></p><ul class="plain">${r.replacements.map((x) => `<li>${esc(x.out)} → ${esc(x.line_ko)} <span class="muted">(점수 ${x.score_delta >= 0 ? "+" : ""}${x.score_delta.toFixed(3)})</span></li>`).join("")}</ul>` : ""}
        </div>
      </div>
    </div>
    <div class="panel"><div class="panel-head"><h2>Showdown 형식</h2><button class="btn" id="rec-copy">복사</button></div>
      <pre class="tail" id="rec-export">${esc(r.export)}</pre>
      <p class="muted">${r.notes.map(esc).join("<br>")} · 계산 ${r.elapsed_s}초, 팀 평가 ${r.evaluations}회</p></div>`;
}
$("#rec-run").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  $("#rec-result").innerHTML = `<div class="panel muted">계산하는 중입니다…</div>`;
  try {
    const r = await api("/api/team/recommend", {
      fix: $("#rec-fix").value, exclude: $("#rec-exclude").value, seed: $("#rec-seed").value,
      validate: Number($("#rec-validate").value), save: $("#rec-save").value,
    });
    $("#rec-result").innerHTML = recHtml(r);
    $("#rec-copy").addEventListener("click", () => navigator.clipboard.writeText(r.export).then(() => toast("복사했습니다.")));
    if (r.saved_as) loadTeams();
  } catch (e) {
    $("#rec-result").innerHTML = "";
    throw e;
  }
}));

const pct0 = (x) => `${Math.round(x * 100)}%`;

// ---------------- 계산기 (데미지 · 스피드) ----------------
// 직접 배틀할 때 대면을 빠르게 확인한다. 실전 탭과는 연결하지 않는다.
// form: 메가 폼 id (메가 폼을 골랐을 때만). 메가는 체크박스가 아니라 포켓몬 목록에서 메가 폼을 골라 정한다
const calcSide = () => ({ species: "", form: "", item: "", ability: "", nature: "", evs: {}, moves: [],
  boosts: { atk: 0, def: 0, spa: 0, spd: 0 }, status: "", hp_pct: 100 });
const calc = { me: calcSide(), opp: calcSide(), filled: { me: false, opp: false }, stats: { me: null, opp: null },
  statsError: { me: "", opp: "" },
  dir: "me", move: { me: "", opp: "" }, hits: { me: null, opp: null }, weather: "", terrain: "", screens: [],
  team: "", teamSets: [], result: null, error: "" };
const CALC_STATS = STAT_KEYS;
const CALC_BOOSTS = [["atk", "공격"], ["def", "방어"], ["spa", "특공"], ["spd", "특방"]];
const CALC_STATUS = [["", "없음"], ["brn", "화상"], ["par", "마비"], ["psn", "독"], ["tox", "맹독"], ["slp", "잠듦"], ["frz", "얼음"]];
const CALC_WEATHER = [["", "날씨 없음"], ["sunnyday", "쾌청"], ["raindance", "비"], ["sandstorm", "모래바람"], ["snowscape", "설경"]];
const CALC_TERRAIN = [["", "필드 없음"], ["electricterrain", "일렉트릭필드"], ["grassyterrain", "그래스필드"], ["psychicterrain", "사이코필드"], ["mistyterrain", "미스트필드"]];
const CALC_SCREENS = [["reflect", "리플렉터"], ["lightscreen", "빛의장막"], ["auroraveil", "오로라베일"]];
const sideLabel = (k) => (k === "me" ? "내" : "상대");
const megaOf = (s) => (s.form ? (builder.options.megas || []).find((m) => m.id === s.form) : null);
const calcName = (o, s) => { const m = megaOf(s); return m ? m.name : nameOf(o.species, s.species); };
const opts = (list, cur) => list.map(([v, n]) => `<option value="${v}" ${String(cur) === String(v) ? "selected" : ""}>${n}</option>`).join("");

async function loadCalc() {
  const o = await builderOptions();
  try {
    const teams = await api("/api/teams");
    calc.teams = teams.map((t) => t.name);
  } catch { calc.teams = []; }
  renderCalc(o);
}
function calcSetPayload(s) {
  return { set: { species: s.species, item: s.item, ability: s.ability, nature: s.nature, evs: s.evs, moves: s.moves },
    mega: !!s.form, boosts: s.boosts, status: s.status, hp_pct: s.hp_pct };
}
// 포켓몬 고르기: 메가 폼을 고르면 메가스톤을 들려 메가로 계산한다. 같은 종의 일반 ↔ 메가 전환은 나머지 세트를 그대로 둔다
async function pickCalcSpecies(k, p) {
  const mega = (builder.options.megas || []).find((m) => m.id === p.id);
  const base = mega ? mega.base : p.id;
  if (!p.id) { calc[k] = calcSide(); calc.filled[k] = false; return; }
  if (calc[k].species !== base) await fillFromStats(k, base);
  calc[k].form = mega ? mega.id : "";
  if (mega) { calc[k].item = mega.stone; await speciesData(mega.id); }
}
async function fillFromStats(k, id) {
  const d = await speciesData(id), p = d.popular, s = calcSide();
  s.species = id;
  s.item = (p.items || [])[0] || "";
  s.ability = (p.abilities || [])[0] || (d.abilities[0] || {}).id || "";
  s.nature = (p.natures || [])[0] || "";
  if ((p.spreads || [])[0]) s.evs = Object.fromEntries(Object.entries(p.spreads[0]).filter(([, v]) => v));
  const learn = new Set(d.moves.map((m) => m.id));
  s.moves = (p.moves || []).filter((m) => learn.has(m)).slice(0, 4);
  calc[k] = s; calc.filled[k] = true; calc.move[k] = "";
}
function calcCard(k, o) {
  const s = calc[k], mega = megaOf(s), sp = mega || o.species.find((x) => x.id === s.species), sd = builder.speciesData[s.species];
  const megaAbility = mega && ((builder.speciesData[mega.id] || {}).abilities || [])[0];
  const item = o.items.find((x) => x.id === s.item), st = calc.stats[k];
  const total = CALC_STATS.reduce((t, [x]) => t + (Number(s.evs[x]) || 0), 0);
  const teamRow = k === "me" ? `<div class="calc-team">
      <select class="input input-sm" data-cteam aria-label="내 팀"><option value="">내 팀에서 불러오기</option>${(calc.teams || []).map((n) => `<option ${n === calc.team ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
      ${calc.teamSets.map((t, i) => { const x = o.species.find((y) => y.id === t.species); return `<button class="calc-member" data-cmember="${i}" title="${esc(x ? x.name : t.species)}">${x ? sprite(x.sprite) : esc(t.species)}</button>`; }).join("")}
    </div>` : "";
  return `<div class="calc-card-head"><h2>${sideLabel(k)} 포켓몬</h2>${calc.filled[k] ? `<span class="state-badge warn">통계로 채움</span>` : ""}</div>
    ${teamRow}
    <div class="calc-mon">${sp ? sprite(sp.sprite) : `<span class="calc-mon-empty"></span>`}
      <div class="calc-mon-main">${pickerBtn(`data-cpick="species" data-side="${k}"`, sp && sp.name, "포켓몬 고르기")}
        ${st ? `<span class="muted small">${(st.types || []).map((t) => TYPE_KO[t] || t).join("·")}</span>` : ""}</div></div>
    ${s.species ? `
    <div class="calc-fields">
      <span class="lbl">성격</span>${pickerBtn(`data-cpick="nature" data-side="${k}"`, nameOf(o.natures, s.nature) && `${nameOf(o.natures, s.nature)} (${(o.natures.find((x) => x.id === s.nature) || {}).note})`, "성격")}
      <span class="lbl">도구</span>${pickerBtn(`data-cpick="item" data-side="${k}"`, item && item.name, "도구")}
      <span class="lbl">특성</span>${mega ? `<span>${esc(megaAbility ? megaAbility.name : "-")} <span class="muted small">(메가 특성)</span></span>`
        : pickerBtn(`data-cpick="ability" data-side="${k}"`, nameOf(sd ? sd.abilities : [], s.ability), "특성")}
    </div>
    <div class="calc-stats">${CALC_STATS.map(([x, n]) => `<label><span>${n}</span>
      <input type="number" min="0" max="32" class="input" data-csp="${x}" data-side="${k}" value="${s.evs[x] || ""}" placeholder="0">
      <b>${st ? st[x] : "-"}</b></label>`).join("")}</div>
    <p class="${total > 66 ? "err" : "muted"} small">SP 합계 ${total}/66 · 숫자 아래는 실수치</p>
    ${calc.statsError[k] ? `<p class="err small">${esc(calc.statsError[k])}</p>` : ""}
    <div class="calc-mods">
      ${CALC_BOOSTS.map(([x, n]) => `<label>${n}<select class="input input-sm" data-cboost="${x}" data-side="${k}">${opts([6, 5, 4, 3, 2, 1, 0, -1, -2, -3, -4, -5, -6].map((v) => [v, v > 0 ? `+${v}` : v]), s.boosts[x] || 0)}</select></label>`).join("")}
      <label>상태<select class="input input-sm" data-cstatus="${k}">${opts(CALC_STATUS, s.status)}</select></label>
      <label>HP %<input type="number" min="1" max="100" class="input input-sm" data-chp="${k}" value="${s.hp_pct}"></label>
    </div>` : ""}`;
}
function calcSpeedLine(o) {
  const a = calc.stats.me, b = calc.stats.opp;
  const bad = ["me", "opp"].filter((k) => calc.statsError[k]);
  if (bad.length) return `<p class="err">${bad.map((k) => `${sideLabel(k)} 포켓몬: ${esc(calc.statsError[k])}`).join(" · ")}</p>`;
  if (!a || !b) return `<p class="muted">양쪽 포켓몬을 고르면 스피드를 비교합니다.</p>`;
  // 상태이상(마비 절반 등)을 반영한 스피드로 비교하고, 바뀐 경우 실수치를 함께 보여 준다
  const sa = a.spe_status ?? a.spe, sb = b.spe_status ?? b.spe;
  const show = (k, st, v) => `${sideLabel(k)} ${esc(calcName(o, calc[k]))} <b>${v}</b>${v !== st.spe ? ` <span class="muted small">(실수치 ${st.spe}, ${(CALC_STATUS.find(([x]) => x === calc[k].status) || [])[1] || ""})</span>` : ""}`;
  const verdict = sa === sb ? "동속 (누가 먼저 움직일지는 무작위)" : sa > sb ? "내가 빠름" : "상대가 빠름";
  return `<div class="calc-speed-row"><span>스피드</span>
    <span class="${sa >= sb ? "calc-win" : ""}">${show("me", a, sa)}</span><span class="muted">vs</span>
    <span class="${sb >= sa ? "calc-win" : ""}">${show("opp", b, sb)}</span>
    <strong>→ ${verdict}</strong></div><p class="muted small">실수치에 상태이상(마비 절반)만 반영합니다 (랭크·스카프·순풍 미반영).</p>`;
}
function hpRangeBar(r) {
  const hp = r.defender.hp, max = r.defender.maxhp;
  const left = (x) => Math.max(0, hp - x) / max * 100;
  const lo = left(r.normal.max), hi = left(r.normal.min);
  return `<div class="calc-hpbar" aria-hidden="true"><i class="rest" style="width:${lo}%"></i><i class="range" style="width:${hi - lo}%"></i></div>`;
}
function calcResultHtml(o) {
  if (calc.error) return `<p class="err">${esc(calc.error)}</p>`;
  const r = calc.result;
  if (!r) return `<p class="muted">공격하는 쪽의 기술을 고르면 계산합니다.</p>`;
  if (r.status) return `<p>${esc(r.move)}은(는) 변화기라 데미지가 없습니다.</p>`;
  if (r.immune) return `<p class="calc-ko">효과가 없습니다</p><p class="muted">${esc(r.move)} → 상대에게 데미지 0</p>`;
  const eff = { 0.25: "효과가 매우 별로 ×0.25", 0.5: "효과가 별로 ×0.5", 1: "", 2: "효과가 굉장함 ×2", 4: "효과가 굉장함 ×4" }[r.effectiveness] ?? `×${r.effectiveness}`;
  const n = r.normal, c = r.crit;
  return `<div class="calc-ko">${esc(n.ko.label)}</div>
    <div class="calc-dmg">${n.min}~${n.max} <span class="muted">(${n.min_pct}~${n.max_pct}%)</span>${r.hits > 1 ? ` <span class="muted small">${r.hits}타 합계</span>` : ""}</div>
    ${hpRangeBar(r)}
    <p class="muted small">HP ${r.defender.hp}/${r.defender.maxhp} 기준${eff ? ` · ${eff}` : ""}</p>
    <p>급소: ${c.min}~${c.max} <span class="muted">(${c.min_pct}~${c.max_pct}%)</span> → ${esc(c.ko.label)}</p>
    <details><summary class="small">난수 16단계 보기</summary><p class="small calc-rolls">${n.rolls.join(", ")}</p>
      <p class="small muted">급소: ${c.rolls.join(", ")}</p></details>
    <p class="muted small">${r.notes.map(esc).join(" · ")}</p>`;
}
// 날씨·필드를 까는 특성(가뭄 등)이면 안내만 한다. 턴이 지나거나 다른 날씨로 덮일 수 있어 자동으로 켜지 않는다
function calcFieldHint(o) {
  const rows = ["me", "opp"].map((k) => [k, (calc.stats[k] || {}).field_hint]).filter(([, h]) => h)
    .filter(([, h]) => (h.weather ? calc.weather !== h.weather : calc.terrain !== h.terrain));
  if (!rows.length) return "";
  return `<div class="calc-hint">${rows.map(([k, h]) => {
    const [kind, list, cur] = h.weather ? ["weather", CALC_WEATHER, h.weather] : ["terrain", CALC_TERRAIN, h.terrain];
    const nm = (list.find(([v]) => v === cur) || [])[1];
    const ab = ((builder.speciesData[calc[k].form || calc[k].species] || {}).abilities || []).find((a) => a.id === h.ability);
    return `<p class="small">${sideLabel(k)} ${esc(calcName(o, calc[k]))}의 ${esc(ab ? ab.name : h.ability)}: 지금 ${esc(nm)}이면
      <button class="btn btn-sm" data-chint="${kind}" data-cval="${cur}">${esc(nm)}로 계산</button></p>`;
  }).join("")}</div>`;
}
function renderCalc(o) {
  o = o || builder.options;
  $("#calc-me").innerHTML = calcCard("me", o);
  $("#calc-opp").innerHTML = calcCard("opp", o);
  $("#calc-speed").innerHTML = calcSpeedLine(o);
  const att = calc.dir, def = att === "me" ? "opp" : "me", asd = builder.speciesData[calc[att].species];
  const mv = asd && asd.moves.find((m) => m.id === calc.move[att]);
  const r = calc.result;
  $("#calc-damage").innerHTML = `<div class="calc-dir">
      <button class="btn btn-sm ${att === "me" ? "btn-primary" : ""}" data-cdir="me">내가 공격</button>
      <button class="btn btn-sm ${att === "opp" ? "btn-primary" : ""}" data-cdir="opp">상대가 공격</button>
      <span class="muted small">${sideLabel(att)} ${esc(calcName(o, calc[att]) || "?")} → ${sideLabel(def)} ${esc(calcName(o, calc[def]) || "?")}</span></div>
    <div class="calc-dmg-controls">
      ${pickerBtn(`data-cmove`, mv && mv.name, calc[att].species ? "기술 고르기" : "먼저 포켓몬을 고르세요")}
      ${r && r.hit_options && r.hit_options.length > 1 ? `<label>타수<select class="input input-sm" data-chits>${opts(r.hit_options.map((h) => [h, `${h}회`]), r.hits)}</select></label>` : ""}
      <select class="input input-sm" data-cweather aria-label="날씨">${opts(CALC_WEATHER, calc.weather)}</select>
      <select class="input input-sm" data-cterrain aria-label="필드">${opts(CALC_TERRAIN, calc.terrain)}</select>
      <span class="calc-screens"><span class="muted small">${sideLabel(def)} 쪽 벽</span>
        ${CALC_SCREENS.map(([v, n]) => `<label class="check"><input type="checkbox" data-cscreen="${v}" ${calc.screens.includes(v) ? "checked" : ""}> ${n}</label>`).join("")}</span>
    </div>
    ${calcFieldHint(o)}
    <div class="calc-result">${calcResultHtml(o)}</div>`;
  bindCalc(o);
}
let calcTimer = null, calcSeq = 0;
function calcRefresh() {
  clearTimeout(calcTimer);
  calcTimer = setTimeout(async () => {
    const seq = ++calcSeq;      // 늦게 도착한 이전 요청의 응답은 버린다 (공격 방향을 바꾼 직후 등)
    const next = { stats: { me: null, opp: null }, statsError: { me: "", opp: "" }, result: null, error: "" };
    const jobs = ["me", "opp"].filter((k) => calc[k].species).map((k) => api("/api/calc/stats", calcSetPayload(calc[k]))
      .then((s) => { next.stats[k] = s; }).catch((e) => { next.statsError[k] = e.message; }));
    const att = calc.dir, def = att === "me" ? "opp" : "me";
    if (calc[att].species && calc[def].species && calc.move[att]) {
      jobs.push(api("/api/calc/damage", { attacker: calcSetPayload(calc[att]), defender: calcSetPayload(calc[def]),
        move: calc.move[att], hits: calc.hits[att], field: { weather: calc.weather, terrain: calc.terrain, screens: calc.screens } })
        .then((r) => { next.result = r; }).catch((e) => { next.error = e.message; }));
    }
    await Promise.all(jobs);
    if (seq !== calcSeq) return;
    Object.assign(calc, next);
    renderCalc();
  }, 250);
}
function calcChanged(k, rerender = true) {
  if (k) calc.filled[k] = false;      // 직접 바꾸면 '통계로 채움' 표시를 끈다
  if (rerender) renderCalc();
  calcRefresh();
}
function bindCalc(o) {
  document.querySelectorAll("[data-cpick]").forEach((btn) => btn.addEventListener("click", async () => {
    const k = btn.dataset.side, s = calc[k], kind = btn.dataset.cpick, sd = await speciesData(s.species);
    if (kind === "species") {
      const ty = (x) => (x.types || []).map((t) => TYPE_KO[t] || t).join("/");
      const list = o.species.flatMap((x) => [{ ...x, note: ty(x) },
        ...(o.megas || []).filter((m) => m.base === x.id).map((m) => ({ ...m, note: `메가 · ${ty(m)}` }))]);
      openPicker(btn, list, async (p) => { await pickCalcSpecies(k, p); renderCalc(); calcRefresh(); }, { allowClear: false });
    } else if (kind === "item") {
      openPicker(btn, withPopular(o.items.map((x) => ({ ...x, note: x.mega ? "메가스톤" : x.berry ? "열매" : "" })), sd && sd.popular.items),
        (p) => { s.item = p.id; if (s.form && p.id !== (megaOf(s) || {}).stone) s.form = ""; calcChanged(k); });
    } else if (kind === "ability") {
      openPicker(btn, withPopular(sd.abilities, sd.popular.abilities), (p) => { s.ability = p.id; calcChanged(k); });
    } else if (kind === "nature") {
      openPicker(btn, withPopular(o.natures, sd && sd.popular.natures), (p) => { s.nature = p.id; calcChanged(k); });
    }
  }));
  const att = calc.dir;
  const mbtn = document.querySelector("[data-cmove]");
  if (mbtn) mbtn.addEventListener("click", async () => {
    const sd = await speciesData(calc[att].species);
    if (!sd) return;
    const cat = (c) => (c === "Status" ? "변화" : c === "Physical" ? "물리" : "특수");
    const list = sd.moves.map((m) => ({ ...m, note: `${TYPE_KO[m.type] || m.type} · ${cat(m.category)}${m.power ? " " + m.power : ""}` }));
    openPicker(mbtn, withPopular(list, [...calc[att].moves, ...(sd.popular.moves || [])]), (p) => {
      calc.move[att] = p.id; calc.hits[att] = null; calcChanged(null);
    }, { allowClear: false });
  });
  document.querySelectorAll("[data-csp]").forEach((inp) => inp.addEventListener("change", () => {
    const s = calc[inp.dataset.side], v = Math.max(0, Math.min(32, Number(inp.value) || 0));
    if (v) s.evs[inp.dataset.csp] = v; else delete s.evs[inp.dataset.csp];
    calcChanged(inp.dataset.side);
  }));
  document.querySelectorAll("[data-cboost]").forEach((sel) => sel.addEventListener("change", () => {
    calc[sel.dataset.side].boosts[sel.dataset.cboost] = Number(sel.value); calcChanged(null, false);
  }));
  document.querySelectorAll("[data-cstatus]").forEach((sel) => sel.addEventListener("change", () => { calc[sel.dataset.cstatus].status = sel.value; calcChanged(null); }));
  document.querySelectorAll("[data-chp]").forEach((inp) => inp.addEventListener("change", () => {
    calc[inp.dataset.chp].hp_pct = Math.max(1, Math.min(100, Number(inp.value) || 100)); calcChanged(null);
  }));
  document.querySelectorAll("[data-cscreen]").forEach((cb) => cb.addEventListener("change", () => {
    calc.screens = CALC_SCREENS.map(([v]) => v).filter((v) => document.querySelector(`[data-cscreen="${v}"]`).checked);
    calcChanged(null, false);
  }));
  document.querySelectorAll("[data-cdir]").forEach((b) => b.addEventListener("click", () => { calc.dir = b.dataset.cdir; calc.result = null; calc.error = ""; calcChanged(null); }));
  const hs = document.querySelector("[data-chits]");
  if (hs) hs.addEventListener("change", () => { calc.hits[att] = Number(hs.value); calcChanged(null, false); });
  document.querySelectorAll("[data-chint]").forEach((b) => b.addEventListener("click", () => {
    calc[b.dataset.chint] = b.dataset.cval; calcChanged(null);
  }));
  document.querySelector("[data-cweather]").addEventListener("change", (e) => { calc.weather = e.target.value; calcChanged(null, false); });
  document.querySelector("[data-cterrain]").addEventListener("change", (e) => { calc.terrain = e.target.value; calcChanged(null, false); });
  const ts = document.querySelector("[data-cteam]");
  if (ts) ts.addEventListener("change", async () => {
    calc.team = ts.value;
    calc.teamSets = ts.value ? (await api("/api/team/show", { name: ts.value }).catch((e) => { toast(e.message); return { sets: [] }; })).sets : [];
    renderCalc();
  });
  document.querySelectorAll("[data-cmember]").forEach((b) => b.addEventListener("click", async () => {
    const t = calc.teamSets[Number(b.dataset.cmember)];
    await speciesData(t.species);
    calc.me = { ...calcSide(), species: t.species, item: t.item, ability: t.ability, nature: t.nature, evs: { ...(t.evs || {}) }, moves: [...(t.moves || [])] };
    calc.filled.me = false; calc.move.me = "";
    renderCalc(); calcRefresh();
  }));
}

// ---------------- 실전 ----------------
const live = { id: null, picks: [], data: null };
async function loadLiveTeams() {
  try {
    const teams = await api("/api/teams");
    const cur = $("#live-team").value;
    $("#live-team").innerHTML = teams.length ? teams.map((t) => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join("")
      : `<option value="">저장한 팀이 없습니다</option>`;
    if (cur && teams.some((t) => t.name === cur)) $("#live-team").value = cur;
    $("#live-team-hint").innerHTML = teams.length ? "팀은 '내 팀'이나 '파티 추천'에서 저장합니다."
      : `먼저 팀을 저장하세요. <a href="#team" data-goto="team">내 팀에서 만들기</a>`;
  } catch (e) { toast(e.message); }
}

$("#live-select").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  if (!$("#live-team").value) { toast("먼저 팀을 저장하세요."); return; }
  $("#live-select-result").innerHTML = `<div class="muted">선출을 계산하는 중입니다… (최대 20초)</div>`;
  const r = await api("/api/live/new", { team: $("#live-team").value, opp: $("#live-opp").value });
  live.id = r.id; live.picks = []; live.data = r; live.lead = null; live.leadOpts = null; live.leadEntry = [];
  $("#live-battle").innerHTML = "";
  renderLiveSetup();
}));

function renderLiveSetup() {
  const r = live.data, s = r.selection;
  const picks = s.picks.slice(0, 4).map((p) => `<li><strong>${pct0(p.probability)}</strong> 선봉 ${esc(p.lead)} + ${esc(p.members.slice(1).join(", "))}
      <span class="muted">(슬롯 ${p.slots.join("·")}${p.mega ? ` · 메가 ${esc(p.mega)}` : ""}) · 예상 승률 ${pct0(p.win_vs_equilibrium)}</span>
      <button class="btn" data-usepick="${p.slots.join(",")}">이대로</button></li>`).join("");
  const mons = r.mine.map((m) => {
    const k = live.picks.indexOf(m.slot);
    return `<button class="pick-btn ${k >= 0 ? "on" : ""}" data-slot="${m.slot}">${sprite(m.sprite)}<span>${esc(m.name)}</span>
      ${k >= 0 ? `<b class="order">${k === 0 ? "선봉" : k + 1}</b>` : ""}</button>`;
  }).join("");
  $("#live-select-result").innerHTML = `
    ${r.notes.length ? `<div class="warn-box panel">${r.notes.map((n) => `<p>${esc(n)}</p>`).join("")}</div>` : ""}
    <div class="grid-2">
      <div><h3>추천 선출 <span class="muted">(이 확률대로 고르세요 · 전체 기대 승률 ${pct1(s.expected_win_vs_equilibrium)})</span></h3><ul class="plain">${picks}</ul></div>
      <div><h3>2. 실제로 낸 선출 <span class="muted">(누른 순서 = 출전 순서, 첫 번째가 선봉)</span></h3>
        <div class="pick-grid">${mons}</div>
        <label>상대 선봉 <select id="live-lead" class="input">${r.opp.map((o) => `<option value="${esc(o.id)}" ${live.lead === o.id ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select></label>
        <div id="live-lead-entry">${entryBoxes(live.leadOpts, live.leadEntry || [], "data-leadentry")}</div>
        <div class="run-row"><button class="btn btn-primary" id="live-start" ${live.picks.length === 3 ? "" : "disabled"}>배틀 시작</button>
          <button class="btn" id="live-clear">선출 다시 고르기</button></div></div>
    </div>`;
  document.querySelectorAll("[data-slot]").forEach((b) => b.addEventListener("click", () => {
    const s = Number(b.dataset.slot), k = live.picks.indexOf(s);
    if (k >= 0) live.picks.splice(k, 1); else if (live.picks.length < 3) live.picks.push(s);
    renderLiveSetup();
  }));
  document.querySelectorAll("[data-usepick]").forEach((b) => b.addEventListener("click", () => {
    live.picks = b.dataset.usepick.split(",").map(Number); renderLiveSetup();
  }));
  $("#live-clear").addEventListener("click", () => { live.picks = []; renderLiveSetup(); });
  const loadLead = async () => {
    live.lead = $("#live-lead").value;
    live.leadOpts = await api("/api/live/entry", { id: live.id, species: live.lead });
    live.leadEntry = defaults(live.leadOpts);
    $("#live-lead-entry").innerHTML = entryBoxes(live.leadOpts, live.leadEntry, "data-leadentry");
    bindLead();
  };
  const bindLead = () => document.querySelectorAll("[data-leadentry]").forEach((c) => c.addEventListener("change", () => {
    live.leadEntry = [...document.querySelectorAll("[data-leadentry]")].filter((x) => x.checked).map((x) => x.dataset.leadentry);
  }));
  $("#live-lead").addEventListener("change", loadLead);
  if (!live.leadOpts || live.lead !== $("#live-lead").value) loadLead(); else bindLead();
  $("#live-start").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
    const v = await api("/api/live/start", { id: live.id, slots: live.picks, opp_lead: $("#live-lead").value, opp_lead_entry: live.leadEntry || [] });
    renderLive(v);
  }));
}

function hpBar(pct) {
  const p = Math.max(0, Math.min(100, pct || 0));
  return `<div class="hpbar"><span class="${hpClass(p)}" style="width:${p}%"></span></div>`;
}
function monCard(m, mine) {
  const boosts = Object.entries(m.boosts || {}).map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`).join(" ");
  const hp = m.fainted ? "기절" : mine && m.hp_abs != null ? `${m.hp_abs}/${m.hp_max}` : `${Math.round(m.hp)}%`;
  return `<div class="live-mon ${m.active ? "active" : ""} ${m.fainted ? "fnt" : ""} ${!mine && !m.seen ? "unseen" : ""}">
    ${sprite(m.sprite)}<div class="body"><div class="nm">${esc(m.name)}${m.mega ? ` <span class="state-badge">메가</span>` : ""}
      ${m.active ? ` <span class="state-badge">출전</span>` : ""}</div>
    ${mine || m.seen ? `${hpBar(m.fainted ? 0 : m.hp)}<div class="muted">${hp}${m.status ? ` · ${esc(m.status)}` : ""}${boosts ? ` · ${esc(boosts)}` : ""}</div>` : `<div class="muted">아직 안 나옴</div>`}
    ${m.moves && m.moves.length ? `<div class="muted small">${esc(m.moves.join(", "))}</div>` : ""}
    ${mine && m.moves_note ? `<div class="muted small">${esc(m.moves_note)}</div>` : ""}
    ${!mine && (m.item || m.ability) ? `<div class="muted small">${esc([m.item && (m.item + (m.item_consumed ? "(사용)" : "")), m.ability].filter(Boolean).join(" · "))}</div>` : ""}</div></div>`;
}
function entryBoxes(opts, chosen, attr) {
  if (!opts || !opts.length) return "";
  return `<div class="entry-box"><span class="muted">나올 때 뜬 메시지:</span> ${opts.map((o) => `<label class="check"><input type="checkbox" ${attr}="${esc(o.key)}"
    ${chosen.includes(o.key) ? "checked" : ""}> ${esc(o.name)} <span class="muted">(${pct0(o.p)})</span></label>`).join(" ")}
    <span class="muted small">위협은 내 공격 −1, 날씨·필드 특성은 날씨·필드, 풍선은 맞으면 터짐까지 자동으로 넣습니다.</span></div>`;
}
const isSwapMove = (a) => a && a.kind === "move" && ["trick", "switcheroo", "트릭", "바꿔치기"].includes(String(a.move || "").trim());
const defaults = (opts) => (opts || []).filter((o) => o.default).map((o) => o.key);
const STATUS_OPTS = [["", "-"], ["brn", "화상"], ["par", "마비"], ["slp", "잠듦"], ["frz", "얼음"], ["psn", "독"], ["tox", "맹독"]];
const WEATHER_OPTS = [["", "-"], ["sun", "쾌청"], ["rain", "비"], ["sand", "모래바람"], ["snow", "설경"], ["none", "날씨 끝"]];
function freshForm(v) {
  const f = v.form || {};
  const best = v.recommendation && v.recommendation.options.find((o) => o.probability > 0);
  return { first: null, me: best && best.action ? { ...best.action } : { kind: null }, opp: { kind: null },
    my_hp: f.my_hp ?? "", opp_hp: f.opp_hp != null ? Math.round(f.opp_hp) : "", edited: { me: false, opp: false }, extras: {} };
}
function renderLive(v) {
  live.view = v;
  if (!live.form || live.form.turn !== v.turn || live.form.phase !== v.phase) { live.form = freshForm(v); live.form.turn = v.turn; live.form.phase = v.phase; }
  const rec = v.recommendation, f = v.form || {}, F = live.form;
  const field = [v.weather && `날씨 ${v.weather.name}${v.weather.remaining ? ` (${v.weather.remaining[0]}${v.weather.remaining[1] !== v.weather.remaining[0] ? "~" + v.weather.remaining[1] : ""}턴)` : ""}`,
    v.terrain && `필드 ${v.terrain.name}${v.terrain.remaining ? ` (${v.terrain.remaining[0]}턴)` : ""}`].filter(Boolean).join(" · ");
  const sel = (cond) => (cond ? " on" : "");
  const steps = v.phase === "replace" ? `<span class="step">① 행동</span> → <span class="step">② 결과</span> → <span class="step now">③ 기절 교체</span>`
    : `<span class="step now">① 행동</span> → <span class="step">② 결과</span> → <span class="step">③ 기절 교체(필요할 때)</span>`;
  const recHtml = rec ? `<div class="panel rec-card"><div class="panel-head"><h2>추천: ${esc(rec.best)}</h2>
      <span class="muted">예상 승률 ${pct0(rec.win)} · ${rec.seconds}초</span></div>
      <table class="table"><tbody>${rec.options.map((o) => `<tr><td>${esc(o.label)}${o.probability > 0 ? " <strong>← 추천</strong>" : ""}</td>
        <td style="width:40%"><div class="bar"><span style="width:${(o.value * 100).toFixed(0)}%"></span></div></td><td>${pct0(o.value)}</td></tr>`).join("")}</tbody></table>
      <button class="btn btn-sm" id="live-why">왜?</button><div id="live-why-box" class="muted"></div></div>` : "";
  let inputHtml = "";
  if (v.phase === "over") {
    inputHtml = `<div class="panel"><p>배틀이 끝났습니다. 아래에서 결과를 골라 끝내세요.</p></div>`;
  } else if (v.phase === "replace") {
    const need = f.need_replace || {};
    inputHtml = `<div class="panel"><div class="panel-head"><h2>기절 교체</h2><span class="muted">${steps}</span></div>
      ${need.opp ? `<p><strong>상대가 다음에 낸 포켓몬은?</strong></p><div class="pick-grid">${f.opp_bench.map((b) => `<button class="pick-btn${sel(F.opp_rep === b.id)}" data-opprep="${esc(b.id)}">${sprite(b.sprite)}<span>${esc(b.name)}${b.seen ? "" : " <span class='muted'>(처음)</span>"}</span></button>`).join("")}</div>
        ${F.opp_rep ? entryBoxes((f.opp_bench.find((b) => b.id === F.opp_rep) || {}).entry, F.opp_rep_entry || [], "data-repentry") : ""}` : ""}
      ${need.me ? `<p><strong>내가 다음에 낸 포켓몬은?</strong> <span class="muted">${rec ? `추천: ${esc(rec.best)}` : ""}</span></p><div class="pick-grid">${f.my_bench.map((b) => `<button class="pick-btn${sel(F.my_rep === b.id)}" data-myrep="${esc(b.id)}">${sprite(b.sprite)}<span>${esc(b.name)}</span></button>`).join("")}</div>` : ""}
      <div class="run-row"><button class="btn btn-primary" id="live-replace" ${(need.opp && !F.opp_rep) || (need.me && !F.my_rep) ? "disabled" : ""}>교체 적용하고 다음 턴</button></div></div>`;
  } else {
    const myMoves = (f.my_moves || []).map((m) => `<button class="opt${sel(F.me.kind === "move" && F.me.move === m.id)}" data-my="move:${esc(m.id)}">${esc(m.name)}</button>`).join("");
    const mySw = (f.my_bench || []).map((b) => `<button class="opt${sel(F.me.kind === "switch" && F.me.species === b.id)}" data-my="switch:${esc(b.id)}">↔ ${esc(b.name)}</button>`).join("");
    const oppMoves = (f.opp_moves || []).map((m) => `<button class="opt${sel(F.opp.kind === "move" && F.opp.move === m.id)}${m.seen ? " seen" : ""}" data-opp="move:${esc(m.id)}" title="${m.seen ? "이미 본 기술" : `추정 ${pct0(m.p)}`}">${esc(m.name)}</button>`).join("");
    const oppSw = (f.opp_bench || []).map((b) => `<button class="opt${sel(F.opp.kind === "switch" && F.opp.species === b.id)}" data-opp="switch:${esc(b.id)}">↔ ${esc(b.name)}</button>`).join("");
    const ready = F.first && F.me.kind && F.opp.kind;
    inputHtml = `<div class="panel live-form"><div class="panel-head"><h2>${v.turn}턴 입력</h2><span class="muted">${steps}</span></div>
      <div class="frow"><span class="flabel">먼저 움직인 쪽</span>
        <button class="opt${sel(F.first === "me")}" data-first="me">나</button><button class="opt${sel(F.first === "opp")}" data-first="opp">상대</button>
        <span class="muted">게임에서 먼저 행동한 쪽 (교체는 항상 먼저라 아무거나 골라도 됩니다)</span></div>
      <div class="frow"><span class="flabel">내 행동</span>${myMoves}
        ${f.my_can_mega ? `<label class="check"><input type="checkbox" id="f-mymega" ${F.me.mega ? "checked" : ""}> 메가진화</label>` : ""}
        <span class="sep"></span>${mySw}<button class="opt${sel(F.me.kind === "none")}" data-my="none:">못 움직임</button></div>
      <div class="frow"><span class="flabel">상대 행동</span>${oppMoves}
        ${pickerBtn(`data-lpick="oppmove"`, F.opp.kind === "move" && !(f.opp_moves || []).some((m) => m.id === F.opp.move) ? (F.opp.moveName || F.opp.move) : "", "다른 기술 찾기")}
        ${f.opp_can_mega ? `<label class="check"><input type="checkbox" id="f-oppmega" ${F.opp.mega ? "checked" : ""}> 메가진화</label>` : ""}
        <span class="sep"></span>${oppSw}<button class="opt${sel(F.opp.kind === "none")}" data-opp="none:">못 움직임</button>
        ${F.opp.kind === "switch" ? entryBoxes(((f.opp_bench || []).find((b) => b.id === F.opp.species) || {}).entry, F.opp.entry || [], "data-oppentry") : ""}</div>
      ${isSwapMove(F.me) || isSwapMove(F.opp) ? `<div class="frow"><span class="flabel">도구 교환</span>
        <span>상대가 준 도구</span> ${pickerBtn(`data-lpick="given"`, F.swap_given ?? f.opp_item_guess ?? "", "도구")}
        <span class="muted">내가 준 도구: ${esc(f.my_item || "없음")} · 받은 도구로 다음 기술부터 계산합니다 (구애 계열이면 그 기술로 고정)</span></div>` : ""}
      ${F.me.kind === "move" && F.me.move === "knockoff" ? `<div class="frow"><span class="flabel">떨어뜨린 도구</span>
        ${pickerBtn(`data-lpick="knocked"`, F.knocked || "", "보였으면 고르기")}
        <span class="muted">게임에 "○○을(를) 떨어뜨렸다"가 떴으면 넣으세요</span></div>` : ""}
      <div class="frow"><span class="flabel">턴 끝 HP</span>
        <label>내 HP <input id="f-myhp" class="input input-sm" type="number" min="0" value="${esc(String(F.my_hp))}"> / ${esc(String(f.my_hp_max || "?"))}</label>
        <button class="btn btn-sm" data-hp="me:0">내 기절</button>
        <label>상대 HP <input id="f-opphp" class="input input-sm" type="number" min="0" max="100" value="${esc(String(F.opp_hp))}">%</label>
        <button class="btn btn-sm" data-hp="opp:0">상대 기절</button>
        <span class="muted" id="f-est">${F.estimated ? "고른 기술로 예상한 값입니다. 게임 화면과 다르면 고치세요." : "행동을 고르면 예상 HP를 채웁니다."}</span></div>
      <details class="frow" ${Object.values(F.extras).some(Boolean) ? "open" : ""}><summary>추가 사항 (있을 때만)</summary>
        <div class="quick">
          <label>내 상태이상 <select id="f-mystatus" class="input input-sm" data-x="me_status">${STATUS_OPTS.map(([k, n]) => `<option value="${k}" ${F.extras.me_status === k ? "selected" : ""}>${n}</option>`).join("")}</select></label>
          <label>상대 상태이상 <select id="f-oppstatus" class="input input-sm" data-x="opp_status">${STATUS_OPTS.map(([k, n]) => `<option value="${k}" ${F.extras.opp_status === k ? "selected" : ""}>${n}</option>`).join("")}</select></label>
          <span>상대 도구 (발동·사용)</span> ${pickerBtn(`data-lpick="oppitem"`, F.extras.opp_item || "", "도구")}
          ${f.my_item_consumable ? `<label class="check"><input type="checkbox" id="f-myitemused" ${F.extras.my_item_used ? "checked" : ""}> 내 ${esc(f.my_item)} 사용</label>` : ""}
          <span>상대 특성 발동</span> ${pickerBtn(`data-lpick="oppability"`, F.extras.opp_ability || "", "특성")}
          <label>날씨 변화 <select id="f-weather" class="input input-sm" data-x="weather">${WEATHER_OPTS.map(([k, n]) => `<option value="${k}" ${F.extras.weather === k ? "selected" : ""}>${n}</option>`).join("")}</select></label>
        </div><p class="muted">랭크 변화(칼춤, 용성군 등 기술의 확정 효과)와 HP 0이면 기절은 자동으로 넣습니다.
        먹다남은음식처럼 계속 발동하는 도구는 처음 한 번만 넣으면 기억해서 다음 턴부터 예상 HP에 회복을 넣고,
        열매·기합의띠처럼 한 번 쓰고 사라지는 도구는 넣으면 자동으로 '사용'으로 처리합니다.</p></details>
      <div id="live-preview" class="muted"></div>
      <div class="run-row"><button class="btn btn-primary" id="live-turn" ${ready ? "" : "disabled"}>이번 턴 적용</button>
        <span class="muted">${ready ? "" : "먼저 움직인 쪽, 내 행동, 상대 행동을 골라 주세요."}</span></div>
      <details><summary>직접 입력 (고급)</summary>
        <textarea id="live-input" class="input" rows="2" placeholder="예: 상대 선공 / 상대 지진 / 내 HP 120/181 / 상대 HP 40%"></textarea>
        <div class="run-row"><button class="btn" id="live-preview-btn">해석 미리보기</button><button class="btn" id="live-apply">문장으로 적용</button></div></details></div>`;
  }
  $("#live-battle").innerHTML = `
    <div class="panel"><div class="panel-head"><h2>${v.phase === "replace" ? "기절 교체" : v.phase === "over" ? "배틀 끝" : `${v.turn}턴`}</h2>
      <span class="muted">${esc(field || "날씨·필드 없음")}${v.mega_used.me ? " · 내 메가 사용함" : ""}${v.mega_used.opp ? " · 상대 메가 사용함" : ""}</span></div>
      <div class="grid-2"><div><h3>나</h3>${v.mine.map((m) => monCard(m, true)).join("")}</div>
        <div><h3>상대</h3>${v.opp.map((m) => monCard(m, false)).join("")}</div></div></div>
    ${v.phase === "replace" ? inputHtml + recHtml : recHtml + inputHtml}
    ${v.warnings.length ? `<div class="panel warn-box">${v.warnings.map((w) => `<p>${esc(w)}</p>`).join("")}</div>` : ""}
    <div class="panel"><div class="panel-head"><h2>지난 입력</h2>
      <span><button class="btn" id="live-win">승리로 끝내기</button> <button class="btn" id="live-loss">패배로 끝내기</button>
        <button class="btn" id="live-abort">중단하기</button></span></div>
      <div id="live-abort-box"></div>
      <ol class="plain small">${v.history.map((h) => `<li>${esc(h)}</li>`).join("") || "<li class='muted'>아직 없습니다.</li>"}</ol></div>`;
  bindLive(v);
}
const rerenderSoon = () => setTimeout(() => live.view && renderLive(live.view), 0);
async function refreshEstimate() {
  const F = live.form;
  if (!F.me.kind || !F.opp.kind) return;
  try {
    const e = await api("/api/live/estimate", { id: live.id, me: F.me, opp: F.opp });
    if (!F.edited.me && e.my_hp != null) { F.my_hp = e.my_hp; if ($("#f-myhp")) $("#f-myhp").value = e.my_hp; }
    if (!F.edited.opp && e.opp_hp != null) { F.opp_hp = e.opp_hp; if ($("#f-opphp")) $("#f-opphp").value = e.opp_hp; }
    F.estimated = true;
    F.extras.my_item_used = !!e.my_item_used;
    if (e.opp_item_used && !F.extras.opp_item) F.extras.opp_item = e.opp_item_used;
    if ($("#f-myitemused")) $("#f-myitemused").checked = F.extras.my_item_used;
    if (e.opp_item_used) rerenderSoon();
    const notes = (e.notes || []).length ? ` (반영: ${e.notes.join(", ")})` : "";
    if ($("#f-est")) $("#f-est").textContent = `고른 기술로 예상한 값입니다${notes}. 게임 화면과 다르면 고치세요.`;
  } catch (e) { /* 예상값은 편의 기능이라 실패해도 넘어간다 */ }
}
function bindLive(v) {
  const F = live.form, rerender = () => renderLive(live.view);
  document.querySelectorAll("[data-first]").forEach((b) => b.addEventListener("click", () => { F.first = b.dataset.first; rerender(); }));
  document.querySelectorAll("[data-my]").forEach((b) => b.addEventListener("click", () => {
    const [kind, id] = b.dataset.my.split(":");
    F.me = kind === "move" ? { kind, move: id, mega: F.me.mega || false } : kind === "switch" ? { kind, species: id } : { kind: "none" };
    rerender(); refreshEstimate();
  }));
  document.querySelectorAll("[data-opp]").forEach((b) => b.addEventListener("click", () => {
    const [kind, id] = b.dataset.opp.split(":");
    F.opp = kind === "move" ? { kind, move: id, mega: F.opp.mega || false } : kind === "switch"
      ? { kind, species: id, entry: defaults(((v.form.opp_bench || []).find((b) => b.id === id) || {}).entry) } : { kind: "none" };
    rerender(); refreshEstimate();
  }));
  document.querySelectorAll("[data-oppentry]").forEach((c) => c.addEventListener("change", () => {
    F.opp.entry = [...document.querySelectorAll("[data-oppentry]")].filter((x) => x.checked).map((x) => x.dataset.oppentry);
  }));
  document.querySelectorAll("[data-lpick]").forEach((btn) => btn.addEventListener("click", async () => {
    const kind = btn.dataset.lpick;
    const oppMon = v.opp.find((m) => m.active) || {};
    if (kind === "oppmove" || kind === "oppability") {
      const sd = await speciesData(oppMon.species);
      if (!sd) return;
      if (kind === "oppmove") {
        const opts = sd.moves.map((m) => ({ ...m, note: `${m.type} · ${m.category === "Status" ? "변화" : m.category === "Physical" ? "물리" : "특수"}${m.power ? " " + m.power : ""}` }));
        openPicker(btn, withPopular(opts, (f.opp_moves || []).map((m) => m.id)), (p) => {
          F.opp = p.id ? { kind: "move", move: p.id, moveName: p.name, mega: F.opp.mega || false } : { kind: null };
          rerender(); refreshEstimate();
        });
      } else {
        openPicker(btn, withPopular(sd.abilities, sd.popular.abilities), (p) => { F.extras.opp_ability = p.name; rerender(); });
      }
      return;
    }
    const o = await builderOptions();
    const guess = o.items.filter((x) => x.name === f.opp_item_guess).map((x) => x.id);
    const items = withPopular(o.items.map((x) => ({ ...x, note: x.mega ? "메가스톤" : x.berry ? "열매" : "" })), guess);
    openPicker(btn, items, (p) => {
      if (kind === "given") F.swap_given = p.name;
      else if (kind === "knocked") F.knocked = p.name;
      else if (kind === "oppitem") F.extras.opp_item = p.name;
      rerender();
    });
  }));
  document.querySelectorAll("[data-x]").forEach((el) => el.addEventListener("change", () => { F.extras[el.dataset.x] = el.value.trim(); }));

  if ($("#f-myitemused")) $("#f-myitemused").addEventListener("change", (e) => { F.extras.my_item_used = e.target.checked; });

  if ($("#f-mymega")) $("#f-mymega").addEventListener("change", (e) => { F.me.mega = e.target.checked; refreshEstimate(); });
  if ($("#f-oppmega")) $("#f-oppmega").addEventListener("change", (e) => { F.opp.mega = e.target.checked; refreshEstimate(); });
  if ($("#f-myhp")) $("#f-myhp").addEventListener("input", (e) => { F.my_hp = e.target.value; F.edited.me = true; });
  if ($("#f-opphp")) $("#f-opphp").addEventListener("input", (e) => { F.opp_hp = e.target.value; F.edited.opp = true; });
  document.querySelectorAll("[data-hp]").forEach((b) => b.addEventListener("click", () => {
    const [side] = b.dataset.hp.split(":");
    if (side === "me") { F.my_hp = 0; F.edited.me = true; $("#f-myhp").value = 0; } else { F.opp_hp = 0; F.edited.opp = true; $("#f-opphp").value = 0; }
  }));
  if ($("#live-turn")) $("#live-turn").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
    const extras = { ...F.extras };
    const form = { first: F.first, me: F.me, opp: F.opp, my_hp: F.my_hp === "" ? null : Number(F.my_hp),
      opp_hp: F.opp_hp === "" ? null : Number(F.opp_hp), extras,
      swap: { given: F.swap_given ?? (v.form.opp_item_guess || "") }, knocked: F.knocked || "" };
    try { renderLive(await api("/api/live/turn", { id: live.id, form })); }
    catch (e) { $("#live-preview").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  }));
  document.querySelectorAll("[data-opprep]").forEach((b) => b.addEventListener("click", () => {
    F.opp_rep = b.dataset.opprep;
    F.opp_rep_entry = defaults(((v.form.opp_bench || []).find((x) => x.id === F.opp_rep) || {}).entry);
    rerender();
  }));
  document.querySelectorAll("[data-repentry]").forEach((c) => c.addEventListener("change", () => {
    F.opp_rep_entry = [...document.querySelectorAll("[data-repentry]")].filter((x) => x.checked).map((x) => x.dataset.repentry);
  }));
  document.querySelectorAll("[data-myrep]").forEach((b) => b.addEventListener("click", () => { F.my_rep = b.dataset.myrep; rerender(); }));
  if ($("#live-replace")) $("#live-replace").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
    renderLive(await api("/api/live/replace", { id: live.id, me: F.my_rep || null, opp: F.opp_rep || null, opp_entry: F.opp_rep_entry || [] }));
  }));
  const ta = $("#live-input");
  if ($("#live-preview-btn")) $("#live-preview-btn").addEventListener("click", async () => {
    const p = await api("/api/live/preview", { id: live.id, text: ta.value });
    $("#live-preview").innerHTML = p.ok ? `해석: ${esc(p.echo)}` : p.errors.map((e) => `<p class="err">해석 실패: ${esc(e)}</p>`).join("");
  });
  if ($("#live-apply")) $("#live-apply").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
    const p = await api("/api/live/preview", { id: live.id, text: ta.value });
    if (!p.ok) { $("#live-preview").innerHTML = p.errors.map((e) => `<p class="err">해석 실패: ${esc(e)}</p>`).join(""); return; }
    renderLive(await api("/api/live/apply", { id: live.id, text: ta.value }));
  }));
  if ($("#live-why")) $("#live-why").addEventListener("click", async () => {
    const w = await api("/api/live/why", { id: live.id });
    const b = w.belief;
    $("#live-why-box").innerHTML = `<p>${esc(w.note)}</p>` + (b ? `<p>상대 ${esc(b.species)} 도구 추정: ${b.items.map(([k, p]) => `${esc(k)} ${pct0(p)}`).join(", ")}
      · 특성: ${b.abilities.map(([k, p]) => `${esc(k)} ${pct0(p)}`).join(", ")} · 메가스톤 ${pct0(b.mega_stone)}</p>` : "");
  });
  const finish = async (result, save = true) => {
    const r = await api("/api/live/end", { id: live.id, result, save });
    live.id = null;
    $("#live-battle").innerHTML = `<div class="panel">${r.battle_id ? `기록을 저장했습니다 (#${r.battle_id}). '복기' 탭에서 턴별 승률을 볼 수 있습니다.`
      : "기록 없이 끝냈습니다."} 새 배틀은 위에서 상대 6마리를 다시 입력하세요.</div>`;
  };
  $("#live-win").addEventListener("click", () => finish("win"));
  $("#live-loss").addEventListener("click", () => finish("loss"));
  $("#live-abort").addEventListener("click", () => {
    $("#live-abort-box").innerHTML = `<div class="warn-box panel"><p>배틀을 중단합니다. 지금까지의 기록을 남길까요?</p>
      <button class="btn" id="abort-save">기록 남기고 중단 (결과 없음)</button> <button class="btn" id="abort-drop">기록 없이 중단</button>
      <button class="btn" id="abort-cancel">계속하기</button></div>`;
    $("#abort-save").addEventListener("click", () => finish("aborted", true));
    $("#abort-drop").addEventListener("click", () => finish("aborted", false));
    $("#abort-cancel").addEventListener("click", () => { $("#live-abort-box").innerHTML = ""; });
  });
}

// ---------------- 복기 ----------------
$("#battle-show-sim").addEventListener("change", loadBattles);
async function loadBattles() {
  try {
    const all = await api("/api/battles");
    const rows = $("#battle-show-sim").checked ? all : all.filter((r) => r.source === "manual");
    $("#battle-list tbody").innerHTML = rows.length ? rows.map((r) => `<tr><td>${r.id}</td><td>${esc((r.played_at || "").slice(0, 16))}</td>
      <td>${r.source === "manual" ? "실제" : "자동"}</td><td>${esc({ win: "승", loss: "패", tie: "무", aborted: "중단" }[r.result] || "-")}</td>
      <td>${esc(r.opp.join(", "))}</td><td><button class="btn btn-sm" data-review="${r.id}">복기</button>
      <button class="btn btn-sm" data-bdel="${r.id}">삭제</button></td></tr>`).join("")
      : `<tr><td colspan="6" class="muted">아직 기록이 없습니다. 실전 탭에서 배틀을 끝내면 여기에 쌓입니다.</td></tr>`;
    document.querySelectorAll("[data-review]").forEach((b) => b.addEventListener("click", (ev) => withBusy(ev.currentTarget, () => runReview(b.dataset.review))));
    document.querySelectorAll("[data-bdel]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm(`배틀 기록 #${b.dataset.bdel}을(를) 지울까요? 되돌릴 수 없습니다.`)) return;
      await api("/api/battle/delete", { id: Number(b.dataset.bdel) });
      $("#review-result").innerHTML = ""; toast("지웠습니다."); loadBattles();
    }));
  } catch (e) { toast(e.message); }
}
async function runReview(id) {
  $("#review-result").innerHTML = `<div class="panel muted">턴마다 다시 계산하는 중입니다… (턴 수 × 몇 초)</div>`;
  try {
    const r = await api("/api/review", { id: Number(id) });
    const flag = { mistake: "실수 후보", unlucky: "정상 판단(운)" };
    $("#review-result").innerHTML = `<div class="panel"><div class="panel-head"><h2>#${r.battle_id} 턴별 승률</h2>
      <span class="muted">결과 ${esc({ win: "승", loss: "패", tie: "무" }[r.result] || "미기록")} · 실수 후보 턴 ${esc(r.mistakes.join(", ") || "없음")}</span></div>
      <div class="review-chart">${r.svg}</div>
      <table class="table"><thead><tr><th>턴</th><th>승률</th><th>실제 수</th><th>최선 수</th><th>손해</th><th>표시</th><th>입력</th></tr></thead><tbody>
      ${r.rows.map((p) => `<tr><td>${p.turn}${p.phase === "switch" ? " (교체)" : ""}</td><td>${p.win == null ? "-" : pct0(p.win)}</td>
        <td>${esc(p.actual)}</td><td>${esc(p.best)}</td><td>${p.regret == null ? "-" : p.regret.toFixed(2)}</td>
        <td>${esc(flag[p.flag] || "")}</td><td class="muted">${esc(p.input)}</td></tr>`).join("")}</tbody></table>
      <p class="muted">승률은 상대 세트 샘플 × 롤아웃으로 다시 계산한 추정입니다. 인게임 결과는 성능 지표가 아니라 복기용입니다.</p></div>`;
  } catch (e) { $("#review-result").innerHTML = ""; throw e; }
}

// ---------------- 공식 공지 ----------------
async function loadNews() {
  try {
    const r = await api("/api/news");
    $("#news-meta").textContent = r.checked_at ? `마지막 확인 ${new Date(r.checked_at).toLocaleString("ko-KR", KST)}, 챔피언스 공지 ${r.notices.length}개` : "아직 확인한 적 없음";
    const now = Date.now() / 1000;
    const important = (n) => [4, 11, 12].includes(n.kind) || /밸런스|레귤레이션|조정|업데이트/.test(n.title);
    $("#news-table tbody").innerHTML = r.notices.map((n) => `<tr style="${n.end_at < now ? "opacity:.6" : ""}">
      <td>${important(n) ? `<span class="state-badge warn" title="엔진이나 데이터에 영향이 있을 수 있음">영향</span>` : ""}</td>
      <td>${fmtTs(n.pub_at)}</td><td>${esc(n.kind_txt)}</td>
      <td><a href="https://news.pokemon-home.com/${r.lang}/page/${n.id}.html" target="_blank" rel="noopener">${esc(n.title)}</a></td>
      <td class="muted">${fmtTs(n.st_at)} ~ ${fmtTs(n.end_at)}</td></tr>`).join("");
  } catch (e) { toast(e.message); }
}
$("#news-check").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const r = await api("/api/news/check", {});
  toast(`새 공지 ${r.new.length}개, 갱신 ${r.updated.length}개, 영향 가능 ${r.important.length}개`);
  loadNews();
}));

// ---------------- 시작 ----------------
loadEngine();
// 다른 탭으로 가는 안내 링크 (예: 팀이 없을 때 '내 팀에서 만들기')
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-goto]");
  if (a) { e.preventDefault(); showTab(a.dataset.goto); }
});
const startTab = (location.hash || "#live").slice(1);
showTab(document.getElementById(`tab-${startTab}`) ? startTab : "live");
