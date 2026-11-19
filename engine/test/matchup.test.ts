import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as E from '../src/engine';

const chomp = { species: 'Garchomp', item: 'Choice Scarf', ability: 'Rough Skin', nature: 'Jolly', evs: { hp: 2, atk: 32, spe: 32 }, moves: ['Earthquake', 'Outrage', 'Rock Slide', 'Fire Fang'] };
const prim = { species: 'Primarina', item: 'Sitrus Berry', ability: 'Torrent', nature: 'Modest', evs: { hp: 32, spa: 32, def: 2 }, moves: ['Moonblast', 'Surf', 'Ice Beam', 'Calm Mind'] };
const zard = { species: 'Charizard', item: 'Charizardite Y', ability: 'Blaze', nature: 'Modest', evs: { hp: 2, spa: 32, spe: 32 }, moves: ['Flamethrower', 'Solar Beam', 'Air Slash', 'Roost'] };

test('matchupFast: 대칭이고 0~1', () => {
  const ab = E.matchupFast({ a: { set: chomp }, b: { set: prim } }).score;
  const ba = E.matchupFast({ a: { set: prim }, b: { set: chomp } }).score;
  assert.ok(ab >= 0 && ab <= 1);
  assert.ok(Math.abs(ab + ba - 1) < 1e-3);
  assert.ok(ab < 0.5);   // 누리레느의 냉동빔이 한카리아스를 한 번에 쓰러뜨린다
});

test('matchupFast: 메가 폼과 등장 시 날씨를 반영한다', () => {
  const r = E.matchupFast({ a: { set: zard, mega: true }, b: { set: prim } });
  assert.equal(r.a.species, 'Charizard-Mega-Y');
  assert.equal(r.weather, 'Sun');
  assert.ok(r.score > 0.5);
  assert.throws(() => E.matchupFast({ a: { set: prim, mega: true }, b: { set: zard } }));
});

test('simulate: 1:1 커스텀 포맷과 최대 데미지 봇', () => {
  const r = E.simulate({ p1: [chomp], p2: [prim], format: 'custom', picks: { p1: 'team 1', p2: 'team 1' }, seed: 't', reps: 20, policy: 'maxdamage' });
  assert.equal(r.wins.unfinished, 0);
  assert.ok(r.p1WinRate < 0.2);
});
