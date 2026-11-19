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
