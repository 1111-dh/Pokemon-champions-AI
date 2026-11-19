// 빠른 근사(matchupFast)와 정밀 시뮬레이션(최대 데미지 봇 1:1)의 일치도를 무작위 세트 쌍으로 잰다.
// 사용법: npx tsx scripts/matchup-agreement.ts [쌍 수=150] [쌍당 판 수=40]
import * as E from '../src/engine';
import { legalRandomTeam } from '../src/teams';

const pairs = Number(process.argv[2] ?? 150);
const reps = Number(process.argv[3] ?? 40);
let agree = 0, decisive = 0, sqErr = 0;
const t0 = performance.now();
for (let i = 0; i < pairs; i++) {
  const a = legalRandomTeam(`ma-${i}-a`).team[i % 6];
  const b = legalRandomTeam(`ma-${i}-b`).team[(i * 5) % 6];
  const sim = E.simulate({ p1: [a], p2: [b], format: 'custom', picks: { p1: 'team 1', p2: 'team 1' }, seed: `ma-${i}`, reps, policy: 'maxdamage' });
  const mega = (s: any) => !!E.matchupFast && /ite( [XYZ])?$/.test(s.item) && s.item !== 'Eviolite';
  let fast: number;
  try { fast = E.matchupFast({ a: { set: a, mega: mega(a) }, b: { set: b, mega: mega(b) } }).score; } catch { fast = 0.5; }
  sqErr += (fast - sim.p1WinRate) ** 2;
  if (Math.abs(sim.p1WinRate - 0.5) >= 0.2) {
    decisive++;
    if ((fast > 0.5) === (sim.p1WinRate > 0.5)) agree++;
  }
}
console.log(JSON.stringify({ pairs, reps, decisive, directionAgreement: +(agree / Math.max(1, decisive)).toFixed(3),
  rmse: +Math.sqrt(sqErr / pairs).toFixed(3), seconds: +((performance.now() - t0) / 1000).toFixed(1) }));
