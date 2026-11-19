// Showdown 엔진과 @smogon/calc의 데미지 계산을 무작위 세트 쌍으로 대조한다.
// 사용법: npx tsx scripts/damage-crosscheck.ts [쌍 수=200]
import { damage } from '../src/damage';
import { legalRandomTeam } from '../src/teams';

const n = Number(process.argv[2] ?? 200);
let cases = 0, mismatches = 0, errors = 0;
const samples: string[] = [];
const byMove = new Map<string, number>();
for (let i = 0; i < n; i++) {
  const [ta, tb] = [legalRandomTeam(`xc-${i}-a`).team, legalRandomTeam(`xc-${i}-b`).team];
  const atk = ta[i % 6], def = tb[(i * 7) % 6];
  for (const mv of atk.moves) {
    try {
      const r = damage({ attacker: { set: atk }, defender: { set: def }, move: mv });
      if (r.status) continue;
      cases++;
      if (r.calc?.mismatch) {
        mismatches++;
        byMove.set(r.move, (byMove.get(r.move) ?? 0) + 1);
        if (samples.length < 12) samples.push(`${atk.species}(${atk.item}/${atk.ability}) ${r.move} → ${def.species}(${def.item}/${def.ability}): 엔진 ${r.rolls[0]}-${r.rolls[15]}, calc ${r.calc.rolls[0]}-${r.calc.rolls[15]}${r.calc.error ? ' ' + r.calc.error : ''}`);
      }
    } catch (e: any) {
      errors++;
      if (samples.length < 12) samples.push(`오류 ${atk.species} ${mv} → ${def.species}: ${e.message}`);
    }
  }
}
console.log(JSON.stringify({ pairs: n, cases, mismatches, errors, mismatchRate: +(mismatches / Math.max(1, cases)).toFixed(4),
  topMismatchMoves: [...byMove.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10) }, null, 2));
for (const s of samples) console.log(' ', s);
