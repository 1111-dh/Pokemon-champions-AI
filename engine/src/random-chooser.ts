// 요청(request)을 보고 합법 선택지 중 하나를 무작위로 고른다. 기준선 봇과 처리량 측정용.
import { PS } from './ps';

export type Rng = () => number;

export function mulberry32(seed: number): Rng {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick<T>(rng: Rng, xs: T[]): T {
  return xs[Math.floor(rng() * xs.length)];
}

function shuffle<T>(rng: Rng, xs: T[]): T[] {
  const a = xs.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/** 요청에 대한 무작위 합법 선택 문자열. 대기 요청이면 null. */
export function randomChoice(request: any, rng: Rng, megaRate = 0.5): string | null {
  if (!request || request.wait) return null;
  const mons: any[] = request.side.pokemon;

  if (request.teamPreview) {
    const size: number = request.maxChosenTeamSize ?? 3;
    const order = shuffle(rng, mons.map((_, i) => i + 1)).slice(0, size);
    return `team ${order.join('')}`;
  }

  const benchSwitches = () =>
    mons
      .map((m, i) => ({ m, slot: i + 1 }))
      .filter(({ m, slot }) => slot > 1 && !m.active && !m.condition.endsWith(' fnt'))
      .map(({ slot }) => `switch ${slot}`);

  if (request.forceSwitch) {
    const opts = benchSwitches();
    return opts.length ? pick(rng, opts) : 'pass';
  }

  const active = request.active[0];
  const moves: string[] = [];
  active.moves.forEach((mv: any, i: number) => {
    if (!mv.disabled && (mv.pp === undefined || mv.pp > 0 || mv.maxpp === undefined)) moves.push(`move ${i + 1}`);
  });
  const opts = [...moves];
  if (!active.trapped && !active.maybeTrapped) opts.push(...benchSwitches());
  if (!opts.length) return 'move 1'; // Struggle 등은 엔진이 처리
  let choice = pick(rng, opts);
  if (choice.startsWith('move') && active.canMegaEvo && rng() < megaRate) choice += ' mega';
  return choice;
}

export { PS };

/**
 * 최대 데미지 봇 (기준선 정책, PROJECT_PLAN 6절 정밀 시뮬레이션): 현재 상대에게 기대 데미지가 가장 큰 기술을 고른다.
 * 메가진화가 가능하면 첫 공격 턴에 한다. 자폭류와 첫 턴이 지난 만나자마자는 고르지 않는다.
 * 쓸 공격 기술이 없거나 모두 막혀 있으면 무작위로 고른다.
 */
/** 날씨로 바뀌는 명중(번개·폭풍: 비 필중·쾌청 50, 눈보라: 눈 필중)을 반영한 명중률. Showdown은 이를 기술의 onModifyMove에서
 *  바꾸지만, 그 이벤트를 직접 부르면 다른 기술에서 난수를 쓰는 부작용이 있을 수 있어 규칙을 여기 적는다. */
function weatherAccuracy(battle: any, move: any): number {
  const w = battle.field.effectiveWeather();
  let acc = move.accuracy;
  if (['thunder', 'hurricane'].includes(move.id)) {
    if (['raindance', 'primordialsea'].includes(w)) acc = true;
    else if (['sunnyday', 'desolateland'].includes(w)) acc = 50;
  }
  if (move.id === 'blizzard' && ['hail', 'snowscape'].includes(w)) acc = true;
  return acc === true ? 1 : acc / 100;
}

export function maxDamageChoice(battle: any, side: any, rng: Rng): string | null {
  const request = side.activeRequest;
  if (!request || request.wait || request.teamPreview || request.forceSwitch) return randomChoice(request, rng, 0);
  const me = side.active[0];
  const foe = side.foe.active[0];
  const active = request.active[0];
  if (!me || !foe || foe.fainted) return randomChoice(request, rng, 0);
  let best: { idx: number; score: number } = { idx: -1, score: 0 };
  const saved = battle.randomizer;
  const tr = battle.trunc;
  battle.randomizer = (base: number) => tr(tr(base * 92) / 100);   // 평균에 가까운 난수
  try {
    active.moves.forEach((mv: any, i: number) => {
      if (mv.disabled) return;
      const move = battle.dex.getActiveMove(mv.id);
      if (move.category === 'Status') return;
      // 자기를 쓰러뜨리는 기술과, 첫 행동이 아니면 실패하는 기술(만나자마자 등)은 고르지 않는다
      if (move.selfdestruct || ['finalgambit', 'memento'].includes(move.id)) return;
      if (['fakeout', 'firstimpression'].includes(move.id) && me.activeMoveActions > 0) return;
      move.willCrit = false;
      const dmg = battle.actions.getDamage(me, foe, move, true);
      const acc = weatherAccuracy(battle, move);
      const score = (typeof dmg === 'number' ? dmg : 0) * acc + rng() * 1e-6;
      if (score > best.score) best = { idx: i, score };
    });
  } finally {
    battle.randomizer = saved;
  }
  if (best.idx < 0) return randomChoice(request, rng, 0);
  return `move ${best.idx + 1}${active.canMegaEvo ? ' mega' : ''}`;
}
