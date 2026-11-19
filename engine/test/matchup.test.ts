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

const pelipper = { species: 'Pelipper', item: 'Damp Rock', ability: 'Drizzle', nature: 'Modest', evs: { hp: 32, spa: 32 }, moves: ['Hurricane', 'Hydro Pump'] };
const ttar = { species: 'Tyranitar', item: 'Leftovers', ability: 'Sand Stream', nature: 'Adamant', evs: { hp: 32, atk: 32 }, moves: ['Stone Edge', 'Crunch'] };
const drill = { species: 'Excadrill', item: 'Focus Sash', ability: 'Sand Rush', nature: 'Jolly', evs: { atk: 32, spe: 32 }, moves: ['Iron Head', 'Rock Slide'] };

test('matchupFast: 날씨 지속 턴은 5턴, 대응 바위면 8턴', () => {
  assert.equal(E.matchupFast({ a: { set: pelipper }, b: { set: chomp } }).weatherTurns, 8);
  assert.equal(E.matchupFast({ a: { set: { ...pelipper, item: 'Leftovers' } }, b: { set: chomp } }).weatherTurns, 5);
});

test('matchupFast: 날씨 스피드 특성 (모래헤치기)', () => {
  const sand = E.matchupFast({ a: { set: drill }, b: { set: ttar } });
  const none = E.matchupFast({ a: { set: drill }, b: { set: { ...ttar, ability: 'Unnerve' } } });
  assert.equal(sand.weather, 'Sand');
  assert.equal(sand.a.speed, none.a.speed * 2);
});

test('matchupFast: 날씨 명중과 날씨부정', () => {
  const alt = { species: 'Altaria', item: 'Leftovers', ability: 'Natural Cure', nature: 'Modest', evs: { hp: 32, spa: 32 }, moves: ['Draco Meteor'] };
  const pel = { ...pelipper, moves: ['Hurricane'] };
  const rain = E.matchupFast({ a: { set: pel }, b: { set: alt } });
  const cloud = E.matchupFast({ a: { set: pel }, b: { set: { ...alt, ability: 'Cloud Nine' } } });
  assert.equal(cloud.weatherSuppressed, true);
  assert.ok(Math.abs(cloud.a.bestPercent / rain.a.bestPercent - 0.7) < 0.02);   // 비 필중 → 날씨부정이면 명중 70
});

test('matchupFast: 사이코필드는 땅에 있는 상대의 선공기를 막는다', () => {
  const indee = { species: 'Indeedee', item: 'Leftovers', ability: 'Psychic Surge', nature: 'Modest', evs: { spa: 32, spe: 32 }, moves: ['Psychic'] };
  const luc = { species: 'Lucario', item: 'Life Orb', ability: 'Inner Focus', nature: 'Adamant', evs: { atk: 32, spe: 32 }, moves: ['Extreme Speed'] };
  const r = E.matchupFast({ a: { set: indee }, b: { set: luc } });
  assert.equal(r.terrain, 'Psychic');
  assert.equal(r.b.bestMove, null);
  assert.ok(r.score > 0.9);
});

test('matchupFast: 그래스필드의 그래스슬라이더 선공', () => {
  const rilla = { species: 'Rillaboom', item: 'Choice Band', ability: 'Grassy Surge', nature: 'Adamant', evs: { hp: 32, atk: 32 }, moves: ['Grassy Glide'] };
  assert.equal(E.matchupFast({ a: { set: rilla }, b: { set: prim } }).a.priority, 1);
  assert.equal(E.matchupFast({ a: { set: { ...rilla, ability: 'Overgrow' } }, b: { set: prim } }).a.priority, 0);
});

test('matchupFast: 확정 부가효과의 상대 능력 하락 (매지컬플레임 특공 −1, 클리어바디는 막음)', () => {
  const fros = { species: 'Froslass', item: 'Leftovers', ability: 'Cursed Body', nature: 'Timid', evs: { hp: 32, spa: 32 }, moves: ['Shadow Ball'] };
  const alc = { species: 'Alcremie', item: 'Leftovers', ability: 'Sweet Veil', nature: 'Modest', evs: { hp: 32, spd: 32 }, moves: ['Mystical Fire'] };
  const drop = E.matchupFast({ a: { set: fros }, b: { set: alc } });
  const noDrop = E.matchupFast({ a: { set: fros }, b: { set: { ...alc, moves: ['Flamethrower'] } } });
  assert.ok(drop.score < noDrop.score);
  const metagross = { species: 'Metagross', item: 'Leftovers', ability: 'Clear Body', nature: 'Adamant', evs: { hp: 32, atk: 32 }, moves: ['Iron Head'] };
  const snarl = { species: 'Incineroar', item: 'Leftovers', ability: 'Blaze', nature: 'Careful', evs: { hp: 32, spd: 32 }, moves: ['Flare Blitz'] };
  const a = E.matchupFast({ a: { set: metagross }, b: { set: { ...snarl, moves: ['Snarl'] } } });
  const b = E.matchupFast({ a: { set: { ...metagross, ability: 'Light Metal' } }, b: { set: { ...snarl, moves: ['Snarl'] } } });
  assert.equal(a.score, b.score);   // 바크아웃은 특공만 내리므로 물리형 메타그로스에는 영향 없음
});

test('simulate: 1:1 커스텀 포맷과 최대 데미지 봇', () => {
  const r = E.simulate({ p1: [chomp], p2: [prim], format: 'custom', picks: { p1: 'team 1', p2: 'team 1' }, seed: 't', reps: 20, policy: 'maxdamage' });
  assert.equal(r.wins.unfinished, 0);
  assert.ok(r.p1WinRate < 0.2);
});
