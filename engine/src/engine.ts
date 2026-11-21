// 엔진 핵심 연산. 서버(server.ts)와 테스트가 함께 쓴다. 모든 함수는 순수하게 입력 → 출력이며,
// 배틀 상태는 Showdown State.serializeBattle 결과(JSON 객체)를 그대로 주고받는다.
import path from 'node:path';
import { PS, FORMAT_ID, toPsSeed } from './ps';
import { maxDamageChoice, mulberry32, randomChoice } from './random-chooser';
import { legalRandomTeam, validateTeam } from './teams';
export { damage } from './damage';
export { matchupFast } from './matchup';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { State } = require(path.resolve(__dirname, '../external/pokemon-showdown/dist/sim/state'));

export type SideId = 'p1' | 'p2';
export type TeamInput = any[] | string; // PokemonSet 배열, packed 문자열, 또는 Showdown 가져오기 텍스트

export function parseTeam(team: TeamInput): any[] {
  if (Array.isArray(team)) return team;
  const sets = team.includes('\n') ? PS.Teams.import(team) : PS.Teams.unpack(team);
  if (!sets) throw new Error('팀을 해석할 수 없다');
  return sets;
}

// ---------- /validate_team ----------
export function validate(req: { team: TeamInput }) {
  const team = parseTeam(req.team);
  const problems = validateTeam(team);
  return { valid: !problems, problems: problems ?? [], team };
}

// ---------- /random_team ----------
/** 시드로 결정되는 무작위 합법 팀 (테스트, 웹 배틀 놀이터용). */
export function randomTeam(req: { seed: string }) {
  const { team } = legalRandomTeam(req.seed);
  return { team, packed: PS.Teams.pack(team), text: PS.Teams.export(team) };
}

// ---------- 배틀 생성과 직렬화 ----------
export interface NewBattleReq {
  p1: TeamInput;
  p2: TeamInput;
  seed: string;
  /** 'bss'(기본, 6마리 중 3마리) 또는 'custom'(팀 크기 자유, 1:1 상성 시뮬레이션용) */
  format?: 'bss' | 'custom';
  /** 팀 프리뷰 선택 (예: "team 312"). 주면 프리뷰를 바로 진행한다. */
  picks?: { p1?: string; p2?: string };
}

function createBattle(req: NewBattleReq) {
  const teams = [req.p1, req.p2].map(t => {
    const team = parseTeam(t);
    validateTeam(team); // 세트를 정규화한다(adjustLevel 등). 위반이어도 막지 않는다(PROJECT_PLAN 4.6).
    return team;
  });
  const battle = new PS.Battle({
    formatid: req.format === 'custom' ? 'gen9championscustomgame' : FORMAT_ID,
    seed: toPsSeed(req.seed),
    p1: { name: 'p1', team: teams[0] },
    p2: { name: 'p2', team: teams[1] },
  });
  if (req.picks?.p1) battle.choose('p1', req.picks.p1);
  if (req.picks?.p2) battle.choose('p2', req.picks.p2);
  return battle;
}

function load(state: any) {
  const battle = State.deserializeBattle(state);
  // 복원된 배틀은 이전 로그를 '보내지 않은' 것으로 보기 때문에, 로그가 1000줄을 넘으면 Showdown이
  // "Infinite loop" 보호 장치로 예외를 던진다. 복원 시점까지의 로그는 이미 전달된 것으로 표시한다.
  battle.sentLogPos = battle.log.length;
  return battle;
}
const save = (battle: any) => State.serializeBattle(battle);

function view(battle: any, logFrom = 0) {
  return {
    state: save(battle),
    turn: battle.turn,
    ended: battle.ended,
    winner: battle.ended ? (battle.winner || null) : undefined,
    requests: Object.fromEntries(battle.sides.map((s: any) => [s.id, s.activeRequest ?? null])),
    log: battle.log.slice(logFrom),
  };
}

export function newBattle(req: NewBattleReq) {
  return view(createBattle(req));
}

// ---------- /legal_actions ----------
export interface LegalAction {
  choice: string;
  kind: 'team' | 'move' | 'switch' | 'pass';
  move?: string;
  mega?: boolean;
  slot?: number;
}

export function legalActionsFromRequest(request: any): LegalAction[] {
  if (!request || request.wait) return [];
  const mons: any[] = request.side.pokemon;
  if (request.teamPreview) {
    // 선봉 × 나머지 2마리 조합 = 6 × C(5,2) = 60가지. 후열 순서는 싱글에서 의미가 없으므로 정렬한다.
    const size: number = request.maxChosenTeamSize ?? 3;
    const out: LegalAction[] = [];
    const idx = mons.map((_, i) => i + 1);
    const combos = (xs: number[], k: number): number[][] =>
      k === 0 ? [[]] : xs.flatMap((x, i) => combos(xs.slice(i + 1), k - 1).map(c => [x, ...c]));
    for (const lead of idx) {
      for (const rest of combos(idx.filter(i => i !== lead), size - 1)) {
        out.push({ choice: `team ${[lead, ...rest].join('')}`, kind: 'team' });
      }
    }
    return out;
  }
  const switches = (): LegalAction[] =>
    mons
      .map((m, i) => ({ m, slot: i + 1 }))
      .filter(({ m, slot }) => slot > 1 && !m.active && !m.condition.endsWith(' fnt'))
      .map(({ slot }) => ({ choice: `switch ${slot}`, kind: 'switch' as const, slot }));
  if (request.forceSwitch) {
    const s = switches();
    return s.length ? s : [{ choice: 'pass', kind: 'pass' }];
  }
  const active = request.active[0];
  const out: LegalAction[] = [];
  active.moves.forEach((mv: any, i: number) => {
    if (mv.disabled) return;
    out.push({ choice: `move ${i + 1}`, kind: 'move', move: mv.id, mega: false });
    if (active.canMegaEvo) out.push({ choice: `move ${i + 1} mega`, kind: 'move', move: mv.id, mega: true });
  });
  if (!active.trapped && !active.maybeTrapped) out.push(...switches());
  return out;
}

export function legalActions(req: { state: any }): Record<SideId, LegalAction[]> {
  const battle = load(req.state);
  return {
    p1: legalActionsFromRequest(battle.getSide('p1').activeRequest),
    p2: legalActionsFromRequest(battle.getSide('p2').activeRequest),
  };
}

// ---------- /step ----------
export function step(req: { state: any; p1?: string; p2?: string }) {
  const battle = load(req.state);
  const from = battle.log.length;
  const errors: Record<string, string> = {};
  for (const sid of ['p1', 'p2'] as SideId[]) {
    const choice = req[sid];
    if (!choice) continue;
    const side = battle.getSide(sid);
    if (!battle.choose(sid, choice)) errors[sid] = side.choice.error || `잘못된 선택: ${choice}`;
  }
  return { ...view(battle, from), errors };
}

// ---------- /simulate ----------
export interface SimulateReq extends Partial<NewBattleReq> {
  /** 시작 상태. 없으면 p1/p2 팀으로 새 배틀을 만든다. */
  state?: any;
  /** 앞부분에 강제할 선택 시퀀스: [[p1선택, p2선택], ...]. 이후는 policy로 진행한다. */
  choices?: [string | null, string | null][];
  policy?: 'random' | 'maxdamage';
  reps?: number;
  seed: string;
  maxTurns?: number;
}

export function simulate(req: SimulateReq) {
  const reps = req.reps ?? 1;
  const maxTurns = req.maxTurns ?? 500;
  const base = req.state ?? save(createBattle(req as NewBattleReq));
  const wins = { p1: 0, p2: 0, tie: 0, unfinished: 0 };
  const games: { winner: string | null; turns: number }[] = [];
  for (let r = 0; r < reps; r++) {
    const battle = load(base);
    // 반복마다 배틀 난수와 정책 난수를 다르게, 그러나 시드로 재현 가능하게 한다.
    battle.prng = new PS.PRNG(toPsSeed(`${req.seed}-rep${r}`));
    const rng = mulberry32(parseInt(toPsSeed(`${req.seed}-policy${r}`).slice(7, 15), 16));
    for (const [c1, c2] of req.choices ?? []) {
      if (c1) battle.choose('p1', c1);
      if (c2) battle.choose('p2', c2);
    }
    while (!battle.ended && battle.turn <= maxTurns) {
      let progressed = false;
      for (const side of battle.sides) {
        if (battle.ended) break;
        const rq = side.activeRequest;
        if (!rq || rq.wait || side.isChoiceDone()) continue;
        const c = req.policy === 'maxdamage' ? maxDamageChoice(battle, side, rng) : randomChoice(rq, rng);
        if (c === null) continue;
        if (!battle.choose(side.id, c)) battle.choose(side.id, 'default');
        progressed = true;
      }
      if (!progressed) break;
    }
    const winner = battle.ended ? (battle.winner || null) : null;
    if (!battle.ended) wins.unfinished++;
    else if (winner === 'p1') wins.p1++;
    else if (winner === 'p2') wins.p2++;
    else wins.tie++;
    games.push({ winner, turns: battle.turn });
  }
  return { reps, wins, p1WinRate: wins.p1 / Math.max(1, reps - wins.unfinished), games };
}

// ---------- /build_state ----------
// 관찰한 배틀 상태(추적기)와 상대 세트 샘플 하나로, 그 시점부터 진행할 수 있는 배틀 상태를 만든다 (단계 5 탐색용, PROJECT_PLAN 9.3).
// 팀 프리뷰를 picks(첫 번호가 지금 출전 중인 포켓몬)로 진행한 뒤, 선출된 3마리의 HP·상태이상·랭크·기절·메가와 날씨·필드를 덮어쓴다.
// 다루지 않는 것(KNOWN_GAPS): 대타출동·씨뿌리기·앵콜·도발·혼란·방어 연속 등 휘발성 상태, PP, 벽·순풍·스텔스록 같은 사이드 상태.
export interface MonPatch {
  hp?: number;            // 정확한 HP (내 쪽)
  hpPct?: number;         // HP % (상대 쪽). 0보다 크면 최소 1
  status?: string;        // brn par slp frz psn tox, '' = 없음
  statusTurns?: number;   // slp: 남은 잠 턴 수(모르면 2), tox: 맹독 단계
  boosts?: Record<string, number>;
  fainted?: boolean;
  mega?: boolean;         // 이미 메가진화했다
  itemConsumed?: boolean; // 도구를 썼다(열매·기합의띠 등)
  choiceLock?: string;    // 구애 계열로 고정된 기술 id (출전 중일 때)
  item?: string;          // 트릭·바꿔치기 등으로 바뀐 지금 도구
}
export interface BuildStateReq {
  p1: TeamInput; p2: TeamInput; seed: string;
  picks: { p1: string; p2: string };
  mons?: { p1?: (MonPatch | null)[]; p2?: (MonPatch | null)[] };   // 선출 순서(picks 순서)대로
  field?: { weather?: string; weatherTurns?: number; terrain?: string; terrainTurns?: number };
  megaUsed?: { p1?: boolean; p2?: boolean };
  turn?: number;
}
const WEATHER_IDS: Record<string, string> = { sun: 'sunnyday', rain: 'raindance', sand: 'sandstorm', snow: 'snowscape' };
const TERRAIN_IDS: Record<string, string> = { electric: 'electricterrain', grassy: 'grassyterrain', psychic: 'psychicterrain',
  misty: 'mistyterrain' };
const BOOST_ZERO = { atk: 0, def: 0, spa: 0, spd: 0, spe: 0, accuracy: 0, evasion: 0 };

export function buildState(req: BuildStateReq) {
  const battle = createBattle({ p1: req.p1, p2: req.p2, seed: req.seed, picks: req.picks });
  if (!battle.getSide('p1').activeRequest || battle.getSide('p1').activeRequest.teamPreview) {
    throw new Error('팀 프리뷰가 끝나지 않았다 (picks 확인)');
  }
  for (const sid of ['p1', 'p2'] as SideId[]) {
    const side = battle.getSide(sid);
    const patches = req.mons?.[sid] ?? [];
    side.pokemon.forEach((p: any, i: number) => {
      const m = patches[i];
      if (!m) return;
      if (m.mega && p.canMegaEvo) battle.actions.runMegaEvo(p);
      if (m.item !== undefined && !m.itemConsumed) {
        p.item = PS.toID(m.item);
        p.itemState = battle.initEffectState({ id: p.item, target: p });
        if (!m.mega) p.canMegaEvo = battle.actions.canMegaEvo(p);   // 메가스톤을 잃었거나 받았을 수 있다
      }
      if (m.itemConsumed) {
        p.lastItem = p.item;
        p.item = '';
        p.itemState = battle.initEffectState({ id: '' });
      }
      if (m.fainted) {
        p.hp = 0;
        p.fainted = true;
        p.status = 'fnt';
        return;
      }
      if (m.hp !== undefined) p.hp = Math.max(1, Math.min(p.maxhp, Math.round(m.hp)));
      else if (m.hpPct !== undefined) p.hp = Math.max(1, Math.min(p.maxhp, Math.round(p.maxhp * m.hpPct / 100)));
      if (m.status !== undefined) {
        p.status = m.status;
        p.statusState = battle.initEffectState({ id: m.status, target: p });
        if (m.status === 'slp') p.statusState.startTime = p.statusState.time = m.statusTurns ?? 2;
        if (m.status === 'tox') p.statusState.stage = m.statusTurns ?? 0;
      }
      if (m.boosts) p.boosts = { ...BOOST_ZERO, ...m.boosts };
      if (m.choiceLock && p.isActive) {
        p.volatiles['choicelock'] = battle.initEffectState({ id: 'choicelock', target: p, move: PS.toID(m.choiceLock) } as any);
      }
    });
    if (req.megaUsed?.[sid]) for (const p of side.pokemon) p.canMegaEvo = false;
    side.pokemonLeft = side.pokemon.filter((p: any) => !p.fainted).length;
    if (side.active[0]?.fainted) throw new Error(`${sid}의 출전 포켓몬이 기절 상태다 (교체를 먼저 정한다)`);
  }
  const f = req.field ?? {};
  const w = WEATHER_IDS[f.weather ?? ''] ?? '';
  battle.field.weather = w;
  battle.field.weatherState = battle.initEffectState(w ? { id: w, duration: Math.max(1, f.weatherTurns ?? 5) } as any : { id: '' });
  const t = TERRAIN_IDS[f.terrain ?? ''] ?? '';
  battle.field.terrain = t;
  battle.field.terrainState = battle.initEffectState(t ? { id: t, duration: Math.max(1, f.terrainTurns ?? 5) } as any : { id: '' });
  if (req.turn) battle.turn = req.turn;
  // 덮어쓴 상태로 기술 막기(구애 고정 등)와 갇힘을 다시 판정한다 (Showdown endTurn과 같은 순서)
  for (const side of battle.sides) {
    for (const p of side.active) {
      if (!p || p.fainted) continue;
      p.maybeDisabled = false;
      p.maybeLocked = false;
      for (const slot of p.moveSlots) { slot.disabled = false; slot.disabledSource = ''; }
      battle.runEvent('DisableMove', p);
      p.trapped = p.maybeTrapped = false;
      battle.runEvent('TrapPokemon', p);
    }
  }
  battle.makeRequest('move');
  return view(battle);
}

// ---------- /stats ----------
// 세트의 실제 능력치(레벨 50, Stat Points). 메가면 메가 폼 기준. 믿음 상태가 파티클마다 한 번 계산해 둔다.
export function stats(req: { set: any; mega?: boolean; status?: string }) {
  const set = { ...req.set, level: 50, moves: req.set.moves?.length ? req.set.moves : ['splash'] };
  const battle = new PS.Battle({ formatid: 'gen9championscustomgame', seed: 'sodium,00000000000000000000000000000000',
    p1: { name: 'a', team: [set] }, p2: { name: 'b', team: [set] } });
  battle.choose('p1', 'team 1');
  battle.choose('p2', 'team 1');
  const m = battle.p1.active[0];
  if (req.mega) {
    if (!battle.actions.canMegaEvo(m)) throw new Error(`${m.species.name}은(는) 지닌 도구로 메가진화할 수 없다`);
    battle.actions.runMegaEvo(m);
  }
  // 상태이상을 반영한 스피드 (마비 절반, 속보 등). 도구·랭크는 빼고 상태이상과 특성만 본다
  let speStatus = m.storedStats.spe;
  if (req.status) {
    m.setStatus(req.status);
    const item = m.item;
    m.item = '' as any;
    speStatus = m.getStat('spe');
    m.item = item;
  }
  return { species: m.species.name, hp: m.maxhp, atk: m.storedStats.atk, def: m.storedStats.def, spa: m.storedStats.spa,
    spd: m.storedStats.spd, spe: m.storedStats.spe, speStatus, ability: m.ability, types: m.getTypes() };
}

// ---------- /bot_choice ----------
// 주어진 상태에서 기준선 봇이 고를 선택 (상대 모델, 검증 대전의 상대). policy: maxdamage(기본) | random
export function botChoice(req: { state: any; side: SideId; policy?: 'maxdamage' | 'random'; seed?: string }) {
  const battle = load(req.state);
  const side = battle.getSide(req.side);
  const rq = side.activeRequest;
  if (!rq || rq.wait) return { choice: null };
  const rng = mulberry32(parseInt(toPsSeed(req.seed ?? 'bot').slice(7, 15), 16));
  const c = req.policy === 'random' ? randomChoice(rq, rng) : maxDamageChoice(battle, side, rng);
  return { choice: c };
}

// ---------- /moves_info ----------
// 웹 실전 입력 폼이 기술의 확정 효과를 자동으로 넣는 데 쓴다: 우선도, 분류, 자기 랭크 변화, 상대 랭크 변화(확정분만).
export function movesInfo(req: { moves: string[] }) {
  const dex = PS.Dex.forFormat(PS.Dex.formats.get(FORMAT_ID));
  const out: Record<string, any> = {};
  for (const id of req.moves) {
    const m = dex.moves.get(id);
    if (!m.exists) continue;
    const self: Record<string, number> = {}, target: Record<string, number> = {};
    if (m.category === 'Status' && m.boosts) Object.assign(m.target === 'self' || m.target === 'adjacentAllyOrSelf' ? self : target, m.boosts);
    if (m.self?.boosts) Object.assign(self, m.self.boosts);
    if (m.secondary?.chance === 100) {
      if (m.secondary.boosts) Object.assign(target, m.secondary.boosts);
      if (m.secondary.self?.boosts) Object.assign(self, m.secondary.self.boosts);
    }
    out[m.id] = { priority: m.priority, category: m.category, selfBoosts: self, targetBoosts: target,
      multihit: !!m.multihit, selfdestruct: !!m.selfdestruct, type: m.type, name: m.name };
  }
  return out;
}
