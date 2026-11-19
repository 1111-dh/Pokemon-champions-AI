import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { damage } from '../src/damage';

const chomp = { species: 'Garchomp', item: 'Garchompite Z', ability: 'Rough Skin', nature: 'Jolly', evs: { hp: 2, atk: 32, spe: 32 } };
const prim = { species: 'Primarina', item: 'Sitrus Berry', ability: 'Torrent', nature: 'Modest', evs: { hp: 32, spa: 32, def: 2 } };

test('damage: 16단계, 급소 1.5배, @smogon/calc와 일치', () => {
  const r = damage({ attacker: { set: chomp }, defender: { set: prim }, move: 'Earthquake' });
  assert.equal(r.rolls.length, 16);
  assert.ok(r.rolls.every((x, i) => i === 0 || x >= r.rolls[i - 1]));       // 85% → 100% 순서
  assert.ok(Math.abs(r.critRolls[15] / r.rolls[15] - 1.5) < 0.02);
  assert.equal(r.calc?.mismatch, false);
  assert.equal(r.defender.maxhp, 187);  // 75 + 80 + 32 (HP 공식: 종족값 + SP + 75)
});

test('damage: 4배 상성은 4배, 무효는 0, 변화기는 status', () => {
  const ice = damage({ attacker: { set: prim }, defender: { set: chomp }, move: 'Ice Beam' });
  assert.equal(ice.typeEffectiveness, 4);
  const imm = damage({ attacker: { set: chomp }, defender: { set: prim }, move: 'Dragon Claw' });
  assert.equal(imm.immune, true);
  assert.equal(imm.typeEffectiveness, 0);
  assert.ok(imm.rolls.every(x => x === 0));
  const st = damage({ attacker: { set: chomp }, defender: { set: prim }, move: 'Stealth Rock' });
  assert.equal(st.status, true);
});

test('damage: 메가 폼은 별도로 계산된다 (메가한카리아스Z는 땅 타입을 잃는다)', () => {
  const base = damage({ attacker: { set: prim }, defender: { set: chomp }, move: 'Ice Beam' });
  const mega = damage({ attacker: { set: prim }, defender: { set: chomp, mega: true }, move: 'Ice Beam' });
  assert.equal(mega.defender.species, 'Garchomp-Mega-Z');
  assert.equal(mega.typeEffectiveness, 2);
  assert.ok(mega.rolls[15] < base.rolls[15]);
  const eqMega = damage({ attacker: { set: chomp, mega: true }, defender: { set: prim }, move: 'Earthquake' });
  const eqBase = damage({ attacker: { set: chomp }, defender: { set: prim }, move: 'Earthquake' });
  assert.ok(eqMega.rolls[15] < eqBase.rolls[15]);            // 자속 보정이 사라진다
  assert.throws(() => damage({ attacker: { set: prim, mega: true }, defender: { set: chomp }, move: 'Surf' }));
});

test('damage: 등장 시 효과(위협, 날씨 특성)는 넣지 않고 요청한 날씨만 적용', () => {
  const pelipper = { species: 'Politoed', item: 'Leftovers', ability: 'Drizzle', nature: 'Modest', evs: { hp: 32, spa: 32, def: 2 } };
  const dry = damage({ attacker: { set: pelipper }, defender: { set: chomp }, move: 'Surf' });
  const rain = damage({ attacker: { set: pelipper }, defender: { set: chomp }, move: 'Surf', field: { weather: 'RainDance' } });
  assert.equal(dry.calc?.mismatch, false);
  assert.ok(rain.rolls[15] > dry.rolls[15] * 1.4);
});

test('damage: 인게임 사례 (docs/damage_cases.json)', (t) => {
  const file = path.resolve(__dirname, '../../docs/damage_cases.json');
  const { cases } = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!cases.length) { t.skip('인게임 사례가 아직 없음'); return; }
  for (const c of cases) {
    const r = damage({ attacker: c.attacker, defender: c.defender, move: c.move, field: c.field });
    const pool = c.crit ? r.critRolls : r.rolls;
    if (c.observed !== undefined) {
      assert.ok(pool.includes(c.observed), `${c.name ?? c.move}: 관찰 ${c.observed}이(가) 엔진 값 ${pool.join(',')}에 없음`);
    } else {
      const shown = pool.map(x => Math.floor((100 * x) / r.defender.maxhp));
      assert.ok(shown.includes(c.observed_percent), `${c.name ?? c.move}: 관찰 ${c.observed_percent}% 가 ${shown.join(',')}에 없음`);
    }
  }
});
