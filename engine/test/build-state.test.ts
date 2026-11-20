import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as E from '../src/engine';
import { PS } from '../src/ps';

const path = require('node:path');
const { State } = require(path.resolve(__dirname, '../external/pokemon-showdown/dist/sim/state'));

const me = [
  { species: 'Garchomp', item: 'Choice Scarf', ability: 'Rough Skin', nature: 'Jolly', evs: { atk: 32, spe: 32 }, moves: ['Earthquake', 'Dragon Claw'] },
  { species: 'Charizard', item: 'Charizardite Y', ability: 'Blaze', nature: 'Timid', evs: { spa: 32, spe: 32 }, moves: ['Flamethrower', 'Solar Beam'] },
  { species: 'Primarina', item: 'Sitrus Berry', ability: 'Torrent', nature: 'Modest', evs: { hp: 32, spa: 32 }, moves: ['Moonblast', 'Surf'] },
];
const opp = [
  { species: 'Tyranitar', item: 'Leftovers', ability: 'Sand Stream', nature: 'Adamant', evs: { hp: 32, atk: 32 }, moves: ['Stone Edge', 'Crunch'] },
  { species: 'Lucario', item: 'Lucarionite', ability: 'Inner Focus', nature: 'Jolly', evs: { atk: 32, spe: 32 }, moves: ['Close Combat', 'Extreme Speed'] },
  { species: 'Gholdengo', item: 'Leftovers', ability: 'Good as Gold', nature: 'Modest', evs: { hp: 32, spa: 32 }, moves: ['Make It Rain', 'Shadow Ball'] },
];

test('buildState: 관찰 상태를 덮어쓰고 그 상태에서 진행된다', () => {
  const v = E.buildState({
    p1: me, p2: opp, seed: 'b', picks: { p1: 'team 213', p2: 'team 213' },
    mons: {
      p1: [{ hp: 120, mega: true, boosts: { spa: 1 } }, { fainted: true }, null],
      p2: [{ hpPct: 40, status: 'brn', boosts: { atk: 2 } }, null, null],
    },
    field: { weather: 'sun', weatherTurns: 3, terrain: 'grassy', terrainTurns: 2 },
    megaUsed: { p1: true }, turn: 7,
  });
  const b = State.deserializeBattle(v.state);
  const a1 = b.getSide('p1').active[0], a2 = b.getSide('p2').active[0];
  assert.equal(a1.species.name, 'Charizard-Mega-Y');
  assert.equal(a1.hp, 120);
  assert.equal(a1.boosts.spa, 1);
  assert.equal(b.getSide('p1').pokemonLeft, 2);                     // 한카리아스 기절
  assert.equal(a2.species.name, 'Lucario');
  assert.equal(a2.status, 'brn');
  assert.equal(a2.boosts.atk, 2);
  assert.ok(Math.abs(a2.hp - Math.round(a2.maxhp * 0.4)) <= 1);
  assert.equal(b.field.weather, 'sunnyday');
  assert.equal(b.field.weatherState.duration, 3);
  assert.equal(b.field.terrain, 'grassyterrain');
  assert.equal(b.turn, 7);
  const la = E.legalActions({ state: v.state });
  assert.ok(la.p1.every(x => !x.mega));                              // 메가는 이미 썼다
  assert.ok(la.p2.some(x => x.mega));                                // 상대는 아직 메가 가능
  assert.ok(!la.p1.some(x => x.kind === 'switch' && x.slot === 2));  // 기절한 포켓몬으로는 교체 불가
  const r = E.step({ state: v.state, p1: 'move 1', p2: 'move 1' });
  assert.deepEqual(r.errors, {});
  assert.ok(r.log.some((l: string) => l.startsWith('|move|')));
});

test('buildState: 구애 고정과 도구 소모', () => {
  const v = E.buildState({
    p1: me, p2: opp, seed: 'c', picks: { p1: 'team 123', p2: 'team 123' },
    mons: { p1: [{ choiceLock: 'dragonclaw' }, null, { itemConsumed: true }] },
  });
  const la = E.legalActions({ state: v.state });
  const moves = la.p1.filter(x => x.kind === 'move');
  assert.deepEqual(moves.map(x => x.move), ['dragonclaw']);         // 고정된 기술만 고를 수 있다
  const b = State.deserializeBattle(v.state);
  assert.equal(b.getSide('p1').pokemon[2].item, '');
  assert.ok(PS);
});
