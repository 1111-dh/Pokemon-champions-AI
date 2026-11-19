// 빠른 근사(matchupFast)와 정밀 시뮬레이션(최대 데미지 봇 1:1)의 일치도를 무작위 세트 쌍으로 잰다.
// 사용법: npx tsx scripts/matchup-agreement.ts [쌍 수=150] [쌍당 판 수=40] [field]
//   field: 적어도 한쪽이 날씨·필드 관련 특성(까는 쪽 또는 쓰는 쪽)인 쌍만 뽑는다.
import * as E from '../src/engine';
import { PS } from '../src/ps';
import { legalRandomTeam } from '../src/teams';

const pairs = Number(process.argv[2] ?? 150);
const reps = Number(process.argv[3] ?? 40);
const fieldOnly = process.argv[4] === 'field';
const FIELD_ABILITIES = new Set(['drought', 'drizzle', 'sandstream', 'snowwarning', 'electricsurge', 'psychicsurge', 'grassysurge',
  'seedsower', 'chlorophyll', 'swiftswim', 'sandrush', 'slushrush', 'surgesurfer', 'solarpower', 'dryskin', 'raindish', 'icebody',
  'sandveil', 'snowcloak', 'sandforce', 'cloudnine', 'megasol']);
const megaOf = (s: any) => /ite( [XYZ])?$/.test(s.item) && s.item !== 'Eviolite';
function fieldSet(s: any): boolean {
  if (FIELD_ABILITIES.has(PS.toID(s.ability))) return true;
  if (!megaOf(s)) return false;
  const dex = PS.Dex.forFormat(PS.Dex.formats.get('gen9championscustomgame'));
  const mega = dex.items.get(s.item).megaStone?.[dex.species.get(s.species).name];
  return !!mega && Object.values(dex.species.get(mega).abilities).some((a: any) => FIELD_ABILITIES.has(PS.toID(a)));
}
function pick(i: number, side: string, slot: number, want: boolean) {
  for (let k = 0; ; k++) {
    const team = legalRandomTeam(`ma-${i}-${side}${k ? `-${k}` : ''}`).team;
    if (!want) return team[slot % 6];
    const hit = team.find(fieldSet);
    if (hit) return hit;
  }
}
let agree = 0, decisive = 0, sqErr = 0;
const t0 = performance.now();
for (let i = 0; i < pairs; i++) {
  const a = pick(i, 'a', i, fieldOnly);
  const b = pick(i, 'b', i * 5, false);
  const sim = E.simulate({ p1: [a], p2: [b], format: 'custom', picks: { p1: 'team 1', p2: 'team 1' }, seed: `ma-${i}`, reps, policy: 'maxdamage' });
  let fast: number;
  try { fast = E.matchupFast({ a: { set: a, mega: megaOf(a) }, b: { set: b, mega: megaOf(b) } }).score; } catch { fast = 0.5; }
  sqErr += (fast - sim.p1WinRate) ** 2;
  if (Math.abs(sim.p1WinRate - 0.5) >= 0.2) {
    decisive++;
    if ((fast > 0.5) === (sim.p1WinRate > 0.5)) agree++;
  }
}
console.log(JSON.stringify({ mode: fieldOnly ? 'field' : 'random', pairs, reps, decisive, directionAgreement: +(agree / Math.max(1, decisive)).toFixed(3),
  rmse: +Math.sqrt(sqErr / pairs).toFixed(3), seconds: +((performance.now() - t0) / 1000).toFixed(1) }));
