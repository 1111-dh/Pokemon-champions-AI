// 포챔 보조 AI 개발 콘솔. 빌드 도구 없이 쓰는 바닐라 JS.
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
const STAT_KO = { hp: "HP", atk: "공격", def: "방어", spa: "특공", spd: "특방", spe: "스피드" };
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
  if (!loaded[name]) { loaded[name] = true; ({ progress: loadProgress, dex: loadDex, news: loadNews, team: loadTeams })[name]?.(); }
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

async function loadEngine() {
  const pill = $("#engine-pill");
  try {
    const e = await api("/api/engine");
    pill.textContent = e.up ? "엔진 연결됨" : "엔진 꺼짐";
    pill.className = `pill ${e.up ? "up" : "down"}`;
  } catch { pill.textContent = "엔진 꺼짐"; pill.className = "pill down"; }
}

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

// ---------------- 배틀 해보기 ----------------
let battle = null;
let picks = [];

$("#battle-new").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  battle = await api("/api/battle/new", { seed: $("#battle-seed").value.trim() });
  picks = [];
  $("#battle-empty").hidden = true;
  $("#battle").hidden = false;
  renderBattle();
}));

async function act(choice, btn) {
  await withBusy(btn, async () => {
    battle = await api("/api/battle/act", { id: battle.id, choice });
    renderBattle();
  });
}

function hpClass(pct) { return pct > 50 ? "" : pct > 20 ? "mid" : "lo"; }

function activeCard(m, mine) {
  if (!m) return `<p class="muted card-empty">${mine ? "선출을 고르면 선봉이 나옵니다." : "아직 나오지 않았습니다."}</p>`;
  const pct = mine ? (m.max_hp ? Math.floor((100 * m.hp) / m.max_hp) : 0) : (m.hp_pct ?? 100);
  const hpText = mine ? `${m.hp ?? 0} / ${m.max_hp ?? "-"}` : `${pct}%`;
  const extra = mine
    ? `${esc(m.ability)} · ${esc(m.item) || "도구 없음"}`
    : [m.ability && `특성 ${esc(m.ability)}`, m.item && `도구 ${esc(m.item)}`, m.moves.length && `본 기술 ${m.moves.map(esc).join(", ")}`].filter(Boolean).join(" / ") || "공개된 정보 없음";
  return `${sprite(m.sprite)}<div>
    <div class="nm">${esc(m.name_ko)}${m.status ? ` <span class="state-badge warn">${esc(m.status)}</span>` : ""}</div>
    ${typeChips(m.types)}
    <div class="hp ${hpClass(pct)}" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="HP"><i style="width:${pct}%"></i></div>
    <div class="hp-text"><span>HP</span><span>${hpText}</span></div>
    <div class="info-line">${extra}</div></div>`;
}

function renderBattle() {
  const b = battle;
  const meActive = b.mine.find((m) => m.active && b.phase !== "team");
  const oppActive = b.opp.find((o) => o.active);
  $("#opp-active").innerHTML = activeCard(oppActive, false);
  $("#me-active").innerHTML = activeCard(meActive, true);

  $("#opp-roster").innerHTML = b.opp.map((o) => `<div class="mon-chip ${o.fainted ? "fainted" : ""} ${o.active ? "active" : ""} ${o.revealed ? "" : "unseen"}"
      title="${o.revealed ? "나온 포켓몬" : "아직 안 나온 포켓몬 (선출됐는지 모름)"}">
      ${sprite(o.sprite)}<span class="nm">${esc(o.name_ko)}</span>
      ${o.revealed ? `<div class="mini-hp"><i style="width:${o.hp_pct ?? 100}%"></i></div>` : ""}</div>`).join("");

  if (b.phase === "team") {
    $("#me-roster").innerHTML = b.mine.map((m) => {
      const order = picks.indexOf(m.slot);
      return `<button class="mon-chip" data-slot="${m.slot}" aria-pressed="${order >= 0}" title="${esc(m.moves.join(", "))}">
        ${order >= 0 ? `<span class="pick-order">${order === 0 ? "선봉" : order + 1}</span>` : ""}
        ${sprite(m.sprite)}<span class="nm">${esc(m.name_ko)}</span></button>`;
    }).join("");
    document.querySelectorAll("#me-roster button").forEach((btn) => btn.addEventListener("click", () => {
      const slot = Number(btn.dataset.slot);
      picks = picks.includes(slot) ? picks.filter((x) => x !== slot) : picks.length < 3 ? [...picks, slot] : picks;
      renderBattle();
    }));
  } else {
    $("#me-roster").innerHTML = b.mine.map((m) => {
      const pct = m.max_hp ? Math.floor((100 * m.hp) / m.max_hp) : 0;
      return `<div class="mon-chip ${m.hp === 0 ? "fainted" : ""} ${m.active ? "active" : ""}" title="${esc(m.moves.join(", "))}">
        ${sprite(m.sprite)}<span class="nm">${esc(m.name_ko)}</span><div class="mini-hp"><i style="width:${pct}%"></i></div></div>`;
    }).join("");
  }
  renderActions();
  renderLog();
}

function renderActions() {
  const b = battle;
  const area = $("#action-area");
  if (b.phase === "ended") {
    area.innerHTML = `<p class="result-banner">${b.winner === "p1" ? "이겼습니다" : b.winner === "p2" ? "졌습니다" : "무승부"}</p>
      <p class="muted">${b.turn}턴, 시드 ${esc(b.seed)}. 같은 시드로 다시 하면 같은 팀으로 시작합니다.</p>`;
    return;
  }
  if (b.phase === "team") {
    const lead = picks[0];
    const rest = picks.slice(1).sort((x, y) => x - y);
    const choice = `team ${[lead, ...rest].join("")}`;
    area.innerHTML = `<h3>선출 3마리를 고르세요</h3>
      <p class="muted">내 포켓몬을 누른 순서대로 정해지고, 처음 누른 포켓몬이 선봉입니다. 메가스톤을 든 포켓몬은 한 마리만 넣는 것이 운용 규칙입니다.</p>
      <div class="run-row"><button class="btn btn-primary" id="confirm-team" ${picks.length === 3 ? "" : "disabled"}>이 순서로 선출</button>
      <span class="muted">${picks.length}/3</span></div>`;
    $("#confirm-team").addEventListener("click", (ev) => act(choice, ev.currentTarget));
    return;
  }
  const title = b.phase === "switch" ? "내보낼 포켓몬을 고르세요" : `${b.turn}턴, 행동을 고르세요`;
  area.innerHTML = `<h3>${title}</h3><div class="actions">${b.actions.map((a, i) => {
    if (a.kind === "move") {
      const sub = [CATEGORY_KO[a.category] || "", TYPE_KO[a.type] || "", a.pp != null ? `PP ${a.pp}/${a.maxpp}` : ""].filter(Boolean).join(" · ");
      return `<button class="act ${a.mega ? "mega" : ""}" data-i="${i}" style="--tc:${TYPE_COLOR[a.type] || "var(--line)"}">${esc(a.label)}<span class="sub">${sub}</span></button>`;
    }
    return `<button class="act" data-i="${i}">${esc(a.label)}<span class="sub">교체</span></button>`;
  }).join("")}</div>`;
  area.querySelectorAll(".act").forEach((btn) => btn.addEventListener("click", () => act(b.actions[Number(btn.dataset.i)].choice, btn)));
}

function renderLog() {
  const log = $("#battle-log");
  log.innerHTML = battle.events.map((e) => `<li class="${e.kind}">${esc(e.text)}</li>`).join("");
  log.scrollTop = log.scrollHeight;
  $("#battle-turn").textContent = battle.turn ? `${battle.turn}턴` : "선출 단계";
}

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
      <div class="statbars">${Object.entries(p.stats).map(([k, v]) => `<span class="muted">${STAT_KO[k]}</span><strong>${v}</strong>
        <span class="statbar"><i style="width:${Math.min(100, (v / max) * 100)}%"></i></span>`).join("")}</div>
      ${megas.length ? `<p class="info-line">메가진화: ${megas.map((m) => `<a href="#" data-id="${m.id}">${esc(m.name_ko)}</a>`).join(", ")}</p>` : ""}
      ${d.sets && d.sets.length ? `<h3 style="margin-top:14px">자주 보이는 세트 (원형 상위 ${d.sets.length}개)</h3>
      <table class="table"><thead><tr><th>확률</th><th>도구</th><th>기술</th><th>성격·배분</th></tr></thead><tbody>
      ${d.sets.map((x) => `<tr><td>${(x.weight * 100).toFixed(1)}%</td><td>${esc(x.item || "-")}${x.mega ? " (메가)" : ""}</td>
        <td>${x.moves.map(esc).join(", ")}</td><td class="muted">${esc(x.nature || "")}${x.spread ? ` ${Object.entries(x.spread).filter(([, v]) => v).map(([k, v]) => `${STAT_KO[k]} ${v}`).join(" / ")}` : ""}</td></tr>`).join("")}
      </tbody></table><p class="muted">${d.sets.some((x) => !x.rule_generated) ? "완성 세트 근거 포함." : "통계의 주변 분포에 규칙 필터와 보정(IPF)을 적용해 만든 추정 세트입니다."}</p>` : ""}
      <h3 style="margin-top:14px">배울 수 있는 기술 ${d.moves.length}개</h3>
      <table class="table"><thead><tr><th>기술</th><th>타입</th><th>분류</th><th>위력</th><th>명중</th><th>PP</th></tr></thead>
      <tbody>${d.moves.map((m) => `<tr title="${esc(m.description)}"><td>${esc(m.name_ko || m.name_en)}</td><td>${typeChip(m.type)}</td>
        <td>${CATEGORY_KO[m.category]}</td><td>${m.power || "-"}</td><td>${m.accuracy ?? "-"}</td><td>${m.max_pp}</td></tr>`).join("")}</tbody></table>`;
    box.querySelectorAll("a[data-id]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); showDex(a.dataset.id); }));
  } catch (e) { box.innerHTML = `<p>${esc(e.message)}</p>`; }
}

// ---------------- 팀 검증 ----------------
$("#team-random").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const r = await api("/api/team/random", {});
  $("#team-text").value = r.text.trim();
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
  const r = await api("/api/team/save", { text, name, fill: $("#team-fill").checked, keep_empty: $("#team-keep").checked });
  $("#team-result").innerHTML = teamResultHtml(r, r.name);
  loadTeams();
}));
async function loadTeams() {
  try {
    const teams = await api("/api/teams");
    $("#team-list").innerHTML = teams.length ? teams.map((t) => `<li><strong>${esc(t.name)}</strong>
      <span class="muted">${t.source === "manual" ? "직접 입력" : "추천"}</span>
      <span>${t.team.map((x) => esc(x.species)).join(", ")}</span></li>`).join("") : `<li class="muted">아직 없습니다.</li>`;
  } catch (e) { $("#team-list").textContent = e.message; }
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
api("/api/progress").then((p) => { $("#branch-pill").textContent = `브랜치 ${p.git.branch}`; }).catch(() => {});
showTab((location.hash || "#progress").slice(1));
