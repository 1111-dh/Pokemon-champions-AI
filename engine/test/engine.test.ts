import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as E from '../src/engine';
import { handle } from '../src/server';
import { legalRandomTeam } from '../src/teams';

const p1 = legalRandomTeam('test-a').team;
const p2 = legalRandomTeam('test-b').team;

test('validate_team: 합법 팀은 통과, 아이템 중복은 위반', () => {
  assert.equal(E.validate({ team: p1 }).valid, true);
  const dup = structuredClone(p1);
  dup[1].item = dup[0].item;
  const r = E.validate({ team: dup });
  assert.equal(r.valid, false);
  assert.ok(r.problems.some((p: string) => p.includes('Item Clause')));
});

test('legal_actions: 팀 프리뷰는 선봉 × 2마리 조합 60가지', () => {
  const b = E.newBattle({ p1, p2, seed: 's' });
  const la = E.legalActions({ state: b.state });
  assert.equal(la.p1.length, 60);
  assert.ok(la.p1.every(a => a.kind === 'team'));
});

test('step: 같은 상태와 같은 선택이면 결과가 같다 (재현성)', () => {
  const b = E.newBattle({ p1, p2, seed: 's', picks: { p1: 'team 123', p2: 'team 123' } });
  assert.equal(b.turn, 1);
  const a1 = E.step({ state: b.state, p1: 'move 1', p2: 'move 1' });
  const a2 = E.step({ state: b.state, p1: 'move 1', p2: 'move 1' });
  assert.deepEqual(a1.log, a2.log);
  assert.equal(a1.turn >= 1, true);
  assert.deepEqual(a1.errors, {});
});

test('step: 잘못된 선택은 오류로 돌려준다', () => {
  const b = E.newBattle({ p1, p2, seed: 's', picks: { p1: 'team 123', p2: 'team 123' } });
  const r = E.step({ state: b.state, p1: 'move 9' });
  assert.ok(r.errors.p1);
});

test('legal_actions: 메가 가능하면 기술마다 메가 선택지가 붙는다', () => {
  // 메가스톤을 든 세트를 찾을 때까지 시드를 바꿔 본다
  for (let i = 0; i < 200; i++) {
    const t = legalRandomTeam(`mega-${i}`).team;
    const idx = t.findIndex((s: any) => /ite( [XYZ])?$/.test(s.item) && s.item !== 'Eviolite');
    if (idx < 0) continue;
    const lead = idx + 1;
    const others = [1, 2, 3, 4, 5, 6].filter(x => x !== lead).slice(0, 2);
    const b = E.newBattle({ p1: t, p2, seed: 'm', picks: { p1: `team ${lead}${others.join('')}`, p2: 'team 123' } });
    const la = E.legalActions({ state: b.state });
    if (!la.p1.some(a => a.mega)) continue;
    assert.equal(la.p1.filter(a => a.mega).length, la.p1.filter(a => a.kind === 'move' && !a.mega).length);
    return;
  }
  assert.fail('메가 가능한 팀을 찾지 못함');
});

test('simulate: 같은 시드는 같은 결과, 반복 수만큼 판을 돌린다', () => {
  const r1 = E.simulate({ p1, p2, seed: 'sim', reps: 20, picks: { p1: 'team 123', p2: 'team 123' } });
  const r2 = E.simulate({ p1, p2, seed: 'sim', reps: 20, picks: { p1: 'team 123', p2: 'team 123' } });
  assert.deepEqual(r1, r2);
  assert.equal(r1.wins.p1 + r1.wins.p2 + r1.wins.tie + r1.wins.unfinished, 20);
  assert.equal(r1.wins.unfinished, 0);
});

test('server: 배치 요청은 항목별 결과를 돌려준다', () => {
  const { status, payload } = handle('/validate_team', { batch: [{ team: p1 }, { team: 'garbage' }] }) as any;
  assert.equal(status, 200);
  assert.equal(payload.results.length, 2);
  assert.equal(payload.results[0].ok, true);
});

test('random_team: 같은 시드는 같은 합법 팀', () => {
  const a = E.randomTeam({ seed: 'rt' });
  const b = E.randomTeam({ seed: 'rt' });
  assert.deepEqual(a.team, b.team);
  assert.equal(a.team.length, 6);
  assert.equal(E.validate({ team: a.team }).valid, true);
});
