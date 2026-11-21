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
        <button class="btn btn-sm" data-tload="${esc(t.name)}">편집으로 불러오기</button>
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
      $("#team-text").value = d.export; $("#team-name").value = d.name; $("#team-replace").checked = true;
      toast("왼쪽 칸에 불러왔습니다. 고친 뒤 '팀 저장'을 누르면 덮어씁니다.");
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

// ---------------- 선출 추천 ----------------
async function loadSelectTeams() {
  try {
    const teams = await api("/api/teams");
    const cur = $("#sel-team").value;
    $("#sel-team").innerHTML = teams.length
      ? teams.map((t) => `<option value="${esc(t.name)}">${esc(t.name)} (${t.source === "manual" ? "직접 입력" : "추천"})</option>`).join("")
      : `<option value="">저장한 팀이 없습니다</option>`;
    if (cur && teams.some((t) => t.name === cur)) $("#sel-team").value = cur;
  } catch (e) { toast(e.message); }
}
const pct0 = (x) => `${Math.round(x * 100)}%`;
function selHtml(r) {
  const m = r.matrix, o = r.oracle;
  const opp = r.opponent.map((x) => `<li class="rec-mon">${sprite(x.sprite)}
      <div><div class="nm">${esc(x.species)}</div>
      <div class="muted">출전 ${pct0(x.bring_probability)}${x.mega_stone_probability ? ` · 메가스톤 ${pct0(x.mega_stone_probability)}` : ""}</div></div></li>`).join("");
  const picks = r.picks.map((p) => `<tr>
      <td><strong>${pct0(p.probability)}</strong><div class="bar"><span style="width:${(p.probability * 100).toFixed(1)}%"></span></div></td>
      <td><div class="pick-mons">${p.sprites.map((sp, i) => sprite(sp, i === 0 ? "lead" : "")).join("")}</div>
        <div>선봉 <strong>${esc(p.lead)}</strong> + ${esc(p.members.slice(1).join(", "))} <span class="muted">(슬롯 ${p.slots.join("·")})</span></div>
        ${p.mega ? `<span class="state-badge">메가: ${esc(p.mega)}</span>` : ""}${p.opponent_model_best_response ? ` <span class="state-badge">상대 모델 최선 응수</span>` : ""}</td>
      <td>${pct0(p.win_vs_equilibrium)} <span class="muted">/ ${pct0(p.win_vs_model)}</span></td>
      <td class="muted">${p.strong.length ? `강함: ${esc(p.strong.join("·"))}` : ""}${p.weak.length ? `<br>불리: ${esc(p.weak.join("·"))}` : ""}</td></tr>`).join("");
  const likely = r.opponent_likely_picks.map((x) => `<li>${pct0(x.probability)} · 선봉 ${esc(x.lead)} + ${esc(x.members.slice(1).join(", "))}</li>`).join("");
  const budget = r.max_games != null ? `${r.max_games.toLocaleString()}판` : `${r.budget_s}초`;
  return `
    ${r.warnings.length ? `<div class="panel warn-box">${r.warnings.map((w) => `<p>${esc(w)}</p>`).join("")}</div>` : ""}
    <div class="panel">
      <div class="panel-head"><h2>추천 선출</h2><span class="muted">${esc(r.team)} · 시드 ${esc(r.seed)} · ${esc(r.freshness)}</span></div>
      <p>이 확률대로 무작위로 고르세요. 전체 기대 승률 <strong>${pct1(r.expected_win_vs_equilibrium)}</strong> (상대가 균형으로 고를 때)
        / ${pct1(r.expected_win_vs_model)} (상대 모델대로 고를 때).</p>
      <table class="table"><thead><tr><th>확률</th><th>선출</th><th>예상 승률 <span class="muted">(균형 / 모델)</span></th><th>이유</th></tr></thead>
        <tbody>${picks}</tbody></table>
      <p class="muted">최종 확률 = ${pct0(1 - r.opponent_model_mix)} × 균형 해 + ${pct0(r.opponent_model_mix)} × 상대 모델 최선 응수.</p>
    </div>
    <div class="grid-2">
      <div class="panel"><div class="panel-head"><h2>상대 6마리</h2></div><ul class="rec-list">${opp}</ul>
        <p class="muted">출전 확률은 상대 균형 전략 기준, 메가스톤 확률은 세트 통계 기준입니다.</p></div>
      <div class="stack">
        <div class="panel"><div class="panel-head"><h2>상대 예상 선출</h2></div><ul class="plain">${likely || "<li class='muted'>-</li>"}</ul></div>
        <div class="panel"><div class="panel-head"><h2>계산</h2></div>
          <div class="kv">
            <span class="k">시간</span><span>${r.elapsed_s}초 (예산 ${budget})</span>
            <span class="k">3:3 시뮬레이션</span><span>${r.games.toLocaleString()}판${r.errors ? ` (오류 ${r.errors})` : ""}</span>
            <span class="k">더블 오라클</span><span>${o.iterations}회 · ${o.converged ? "수렴" : "예산에서 멈춤 (수렴 전)"}</span>
            <span class="k">보수 행렬</span><span>${m.size[0]}×${m.size[1]} 중 정밀 ${m.precise_cells}칸 (근사값만 ${pct0(m.approx_only_share)})</span>
            <span class="k">후보</span><span>${m.candidates[0]}×${m.candidates[1]} · 정밀 칸 ${pct0(m.candidate_precise_share)}</span>
            <span class="k">정밀값 비중</span><span>${pct0(m.weighted_precise_share)} <span class="muted">(균형 확률로 가중)</span></span>
          </div>
          <p class="muted">${esc(r.policy)}</p></div>
      </div>
    </div>
    <details class="panel"><summary>글로 보기 (보수 행렬 포함)</summary><pre class="tail">${esc(r.text)}</pre></details>`;
}
$("#sel-run").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  if (!$("#sel-team").value) { toast("먼저 팀을 저장하세요."); return; }
  $("#sel-result").innerHTML = `<div class="panel muted">계산하는 중입니다… (최대 ${$("#sel-budget").value}초)</div>`;
  try {
    const r = await api("/api/select", { team: $("#sel-team").value, opp: $("#sel-opp").value, seed: $("#sel-seed").value,
      budget: Number($("#sel-budget").value) });
    $("#sel-result").innerHTML = selHtml(r);
  } catch (e) {
    $("#sel-result").innerHTML = "";
    throw e;
  }
}));
document.querySelector('.tabs button[data-tab="select"]').addEventListener("click", loadSelectTeams);

// ---------------- 실전 ----------------
const live = { id: null, picks: [], data: null };
async function loadLiveTeams() {
  try {
    const teams = await api("/api/teams");
    const cur = $("#live-team").value;
    $("#live-team").innerHTML = teams.length ? teams.map((t) => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join("")
      : `<option value="">저장한 팀이 없습니다</option>`;
    if (cur && teams.some((t) => t.name === cur)) $("#live-team").value = cur;
  } catch (e) { toast(e.message); }
}
document.querySelector('.tabs button[data-tab="live"]').addEventListener("click", loadLiveTeams);

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
    ${!mine && (m.item || m.ability) ? `<div class="muted small">${esc([m.item && (m.item + (m.item_consumed ? "(사용)" : "")), m.ability].filter(Boolean).join(" · "))}</div>` : ""}</div></div>`;
}
function entryBoxes(opts, chosen, attr) {
  if (!opts || !opts.length) return "";
  return `<div class="entry-box"><span class="muted">나올 때 뜬 메시지:</span> ${opts.map((o) => `<label class="check"><input type="checkbox" ${attr}="${esc(o.key)}"
    ${chosen.includes(o.key) ? "checked" : ""}> ${esc(o.name)} <span class="muted">(${pct0(o.p)})</span></label>`).join(" ")}
    <span class="muted small">위협은 내 공격 −1, 날씨·필드 특성은 날씨·필드, 풍선은 맞으면 터짐까지 자동으로 넣습니다.</span></div>`;
}
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
        <input id="f-oppother" class="input input-sm" list="f-oppmoves" placeholder="다른 기술" value="${F.opp.kind === "move" && !(f.opp_moves || []).some((m) => m.id === F.opp.move) ? esc(F.opp.move || "") : ""}">
        <datalist id="f-oppmoves">${(f.opp_all_moves || []).map((n) => `<option value="${esc(n)}">`).join("")}</datalist>
        ${f.opp_can_mega ? `<label class="check"><input type="checkbox" id="f-oppmega" ${F.opp.mega ? "checked" : ""}> 메가진화</label>` : ""}
        <span class="sep"></span>${oppSw}<button class="opt${sel(F.opp.kind === "none")}" data-opp="none:">못 움직임</button>
        ${F.opp.kind === "switch" ? entryBoxes(((f.opp_bench || []).find((b) => b.id === F.opp.species) || {}).entry, F.opp.entry || [], "data-oppentry") : ""}</div>
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
          <label>상대 도구 발동 <input id="f-oppitem" class="input input-sm" data-x="opp_item" placeholder="기합의띠" value="${esc(F.extras.opp_item || "")}"></label>
          <label>상대 특성 발동 <input id="f-oppability" class="input input-sm" data-x="opp_ability" placeholder="위협" value="${esc(F.extras.opp_ability || "")}"></label>
          <label>날씨 변화 <select id="f-weather" class="input input-sm" data-x="weather">${WEATHER_OPTS.map(([k, n]) => `<option value="${k}" ${F.extras.weather === k ? "selected" : ""}>${n}</option>`).join("")}</select></label>
        </div><p class="muted">랭크 변화(칼춤, 용성군 등 기술의 확정 효과)와 HP 0이면 기절은 자동으로 넣습니다.</p></details>
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
async function refreshEstimate() {
  const F = live.form;
  if (!F.me.kind || !F.opp.kind) return;
  try {
    const e = await api("/api/live/estimate", { id: live.id, me: F.me, opp: F.opp });
    if (!F.edited.me && e.my_hp != null) { F.my_hp = e.my_hp; if ($("#f-myhp")) $("#f-myhp").value = e.my_hp; }
    if (!F.edited.opp && e.opp_hp != null) { F.opp_hp = e.opp_hp; if ($("#f-opphp")) $("#f-opphp").value = e.opp_hp; }
    F.estimated = true;
    if ($("#f-est")) $("#f-est").textContent = "고른 기술로 예상한 값입니다. 게임 화면과 다르면 고치세요.";
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
  if ($("#f-oppother")) $("#f-oppother").addEventListener("change", (e) => {
    if (e.target.value.trim()) { F.opp = { kind: "move", move: e.target.value.trim(), mega: F.opp.mega || false }; rerender(); refreshEstimate(); }
  });
  document.querySelectorAll("[data-x]").forEach((el) => el.addEventListener("change", () => { F.extras[el.dataset.x] = el.value.trim(); }));
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
      opp_hp: F.opp_hp === "" ? null : Number(F.opp_hp), extras };
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
async function loadBattles() {
  try {
    const rows = await api("/api/battles");
    $("#battle-list tbody").innerHTML = rows.length ? rows.map((r) => `<tr><td>${r.id}</td><td>${esc((r.played_at || "").slice(0, 16))}</td>
      <td>${r.source === "manual" ? "실제" : "자동"}</td><td>${esc({ win: "승", loss: "패", tie: "무", aborted: "중단" }[r.result] || "-")}</td>
      <td>${esc(r.opp.join(", "))}</td><td><button class="btn btn-sm" data-review="${r.id}">복기</button>
      <button class="btn btn-sm" data-bdel="${r.id}">삭제</button></td></tr>`).join("")
      : `<tr><td colspan="6" class="muted">아직 기록이 없습니다.</td></tr>`;
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
document.querySelector('.tabs button[data-tab="review"]').addEventListener("click", loadBattles);

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
