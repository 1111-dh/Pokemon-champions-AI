import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as E from '../src/engine';

const path = require('node:path');
const { State } = require(path.resolve(__dirname, '../external/pokemon-showdown/dist/sim/state'));

const team = (species: string[]) => species.map((s) => ({ species: s, nature: 'Hardy', moves: ['Tackle'] }));

test('build_state side conditions and trick room', () => {
  const v = E.buildState({
    p1: team(['Garchomp', 'Dragonite']), p2: team(['Corviknight', 'Aggron']), seed: 'f',
    picks: { p1: 'team 12', p2: 'team 12' },
    sideConditions: { p2: { reflect: 3, spikes: 2 }, p1: { tailwind: 2, stealthrock: 1 } }, trickRoom: 2,
  } as any);
  const b = State.deserializeBattle(v.state);
  assert.equal(b.p2.sideConditions.reflect.duration, 3);
  assert.equal(b.p2.sideConditions.spikes.layers, 2);
  assert.equal(b.p1.sideConditions.tailwind.duration, 2);
  assert.ok(b.p1.sideConditions.stealthrock);
  assert.equal(b.field.pseudoWeather.trickroom.duration, 2);
});

const entry = (species: string, hazards: Record<string, number>) =>
  (E as any).entryHazards({ set: { species, nature: 'Hardy', moves: ['Tackle'] }, hazards });

test('entry hazards stealth rock', () => {
  const r = entry('Charizard', { stealthrock: 1 });     // 불꽃·비행: 바위 4배 → 1/2
  assert.equal(r.damage, Math.floor(r.maxhp / 2));
});

test('entry hazards grounded only', () => {
  assert.equal(entry('Corviknight', { spikes: 3 }).damage, 0);       // 비행은 압정 면제
  const g = entry('Garchomp', { spikes: 1 });
  assert.equal(g.damage, Math.floor(g.maxhp / 8));
});

test('entry hazards toxic spikes', () => {
  assert.equal(entry('Garchomp', { toxicspikes: 2 }).status, 'tox');
  assert.equal(entry('Garchomp', { toxicspikes: 1 }).status, 'psn');
  const g = entry('Gengar', { toxicspikes: 2 });                      // 독 타입은 흡수: 독압정이 사라진다
  assert.equal(g.status, '');
  assert.equal(g.absorbed, true);
  assert.equal(entry('Garchomp', { toxicspikes: 2 }).absorbed, false);
});

test('entry hazards sticky web', () => {
  assert.equal(entry('Garchomp', { stickyweb: 1 }).boosts.spe, -1);
});
