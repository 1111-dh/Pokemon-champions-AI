// 단계 0: 챔피언스 싱글(BSS Reg M-C)에서 무작위 봇끼리 N판을 돌려 오류 여부와 처리량을 잰다.
// 사용법: npm run selfplay -- [판수=1000] [기본시드=stage0]
import path from 'node:path';
import { PS, FORMAT_ID, toPsSeed } from './ps';
import { mulberry32, randomChoice } from './random-chooser';
import { legalRandomTeam } from './teams';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { State } = require(path.resolve(__dirname, '../external/pokemon-showdown/dist/sim/state'));

const MAX_TURNS = 500;

interface GameResult {
  seed: string;
  winner: string | null;
  turns: number;
  ms: number;
  error?: string;
  stalled?: boolean;
  invalidTeams: number;
  megaUsed: number;
}

export function playOne(baseSeed: string, i: number): GameResult {
  const seed = `${baseSeed}-${i}`;
  const rng = mulberry32(parseInt(toPsSeed(seed).slice(7, 15), 16));
  const built = [1, 2].map(p => legalRandomTeam(`${seed}-team${p}`));
  const teams = built.map(b => b.team);
  const invalidTeams = 0; // legalRandomTeam이 검증 통과 팀만 돌려준다
  const levels = teams.flat().map((s: any) => s.adjustLevel ?? s.level);
  if (levels.some((l: number) => l !== 50)) throw new Error(`레벨 50이 아닌 세트: ${levels}`);
  const t0 = performance.now();
  const battle = new PS.Battle({
    formatid: FORMAT_ID,
    seed: toPsSeed(`${seed}-battle`),
    p1: { name: 'p1', team: teams[0] },
    p2: { name: 'p2', team: teams[1] },
  });
  try {
    let guard = 0;
    while (!battle.ended) {
      if (battle.turn > MAX_TURNS || ++guard > MAX_TURNS * 10) {
        return { seed, winner: null, turns: battle.turn, ms: performance.now() - t0, stalled: true, invalidTeams, megaUsed: 0 };
      }
      for (const side of battle.sides) {
        if (battle.ended) break;
        const req = side.activeRequest;
        if (!req || req.wait || side.isChoiceDone()) continue;
        const choice = randomChoice(req, rng);
        if (choice === null) continue;
        if (!battle.choose(side.id, choice)) {
          side.clearChoice?.();
          if (!battle.choose(side.id, 'default')) throw new Error(`선택 실패: ${side.id} "${choice}" / ${side.choice.error}`);
        }
      }
    }
    const megaLines = battle.log.filter((l: string) => l.startsWith('|-mega|'));
    const megaUsed = megaLines.length;
    for (const sid of ['p1', 'p2']) {
      const k = megaLines.filter((l: string) => l.startsWith(`|-mega|${sid}`)).length;
      if (k > 1) throw new Error(`${sid}가 한 판에 메가진화를 ${k}번 했다`);
    }
    return { seed, winner: battle.winner || null, turns: battle.turn, ms: performance.now() - t0, invalidTeams, megaUsed };
  } catch (e: any) {
    return { seed, winner: null, turns: battle.turn, ms: performance.now() - t0, error: String(e?.stack ?? e), invalidTeams, megaUsed: 0 };
  }
}

/** 배틀 중간 상태의 직렬화/복원(탐색 분기용) 처리량을 잰다. */
export function measureClone(baseSeed: string, reps = 2000) {
  const teams = [1, 2].map(p => legalRandomTeam(`${baseSeed}-clone${p}`).team);
  const battle = new PS.Battle({ formatid: FORMAT_ID, seed: toPsSeed(`${baseSeed}-clone`), p1: { name: 'p1', team: teams[0] }, p2: { name: 'p2', team: teams[1] } });
  const rng = mulberry32(7);
  // 몇 턴 진행시켜 중간 상태를 만든다
  while (!battle.ended && battle.turn < 3) {
    for (const side of battle.sides) {
      const req = side.activeRequest;
      if (!req || req.wait || side.isChoiceDone()) continue;
      const c = randomChoice(req, rng, 0);
      if (c && !battle.choose(side.id, c)) battle.choose(side.id, 'default');
    }
  }
  let t0 = performance.now();
  let json: any;
  for (let i = 0; i < reps; i++) json = State.serializeBattle(battle);
  const serMs = (performance.now() - t0) / reps;
  t0 = performance.now();
  for (let i = 0; i < reps; i++) State.deserializeBattle(json);
  const desMs = (performance.now() - t0) / reps;
  return { serializeMs: serMs, deserializeMs: desMs, clonesPerSec: 1000 / (serMs + desMs), atTurn: battle.turn };
}

function main() {
  const n = Number(process.argv[2] ?? 1000);
  const baseSeed = process.argv[3] ?? 'stage0';
  const results: GameResult[] = [];
  const t0 = performance.now();
  for (let i = 0; i < n; i++) results.push(playOne(baseSeed, i));
  const wall = (performance.now() - t0) / 1000;

  const errors = results.filter(r => r.error);
  const stalled = results.filter(r => r.stalled);
  const finished = results.filter(r => !r.error && !r.stalled);
  const turns = finished.map(r => r.turns).sort((a, b) => a - b);
  const totalTurns = turns.reduce((a, b) => a + b, 0);
  const summary = {
    format: FORMAT_ID,
    baseSeed,
    games: n,
    finished: finished.length,
    errors: errors.length,
    stalled: stalled.length,
    p1Wins: finished.filter(r => r.winner === 'p1').length,
    ties: finished.filter(r => !r.winner).length,
    gamesWithMega: finished.filter(r => r.megaUsed > 0).length,
    maxMegaInOneGame: Math.max(0, ...finished.map(r => r.megaUsed)),
    gamesWithInvalidTeam: results.filter(r => r.invalidTeams > 0).length,
    turnsMedian: turns[Math.floor(turns.length / 2)] ?? 0,
    turnsMax: turns[turns.length - 1] ?? 0,
    wallSec: +wall.toFixed(2),
    gamesPerSec: +(n / wall).toFixed(1),
    turnsPerSec: +(totalTurns / wall).toFixed(0),
    clone: measureClone(baseSeed),
    node: process.version,
  };
  console.log(JSON.stringify(summary, null, 2));
  for (const e of errors.slice(0, 5)) console.error(`\n[error] ${e.seed}\n${e.error}`);
  if (errors.length || stalled.length) process.exitCode = 1;
}

if (require.main === module) main();
