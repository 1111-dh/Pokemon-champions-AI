// 엔진 핵심 연산. 서버(server.ts)와 테스트가 함께 쓴다. 모든 함수는 순수하게 입력 → 출력이며,
// 배틀 상태는 Showdown State.serializeBattle 결과(JSON 객체)를 그대로 주고받는다.
import path from 'node:path';
import { PS, FORMAT_ID, toPsSeed } from './ps';
import { mulberry32, randomChoice } from './random-chooser';
import { legalRandomTeam, validateTeam } from './teams';

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
    formatid: FORMAT_ID,
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
  policy?: 'random';
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
        const c = randomChoice(rq, rng);
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
