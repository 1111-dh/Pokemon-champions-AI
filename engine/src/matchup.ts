// 1:1 상성 빠른 근사 (PROJECT_PLAN 6절). 서로 최고 기대 데미지 기술로 몇 번 만에 쓰러뜨리는지(n타)와
// 선공 여부로 0~1 점수를 낸다. 대량 계산용이라 @smogon/calc(엔진과 교차 검증 97.9% 일치)를 쓴다.
// 점수는 대칭이다: score(A,B) + score(B,A) = 1.
import { calculate, Field, Generations, Move, Pokemon } from '@smogon/calc';
import { PS } from './ps';

const gen = Generations.get(0 as any);
const dex = PS.Dex.forFormat(PS.Dex.formats.get('gen9championscustomgame'));
const SCORE_K = 2.5;  // 승패가 갈리는 한 수 차이(±0.5)에서 약 0.78/0.22

export interface MatchupSet { set: any; mega?: boolean }

interface SideEval {
  species: string;
  bestMove: string | null;
  bestPercent: number;      // 기대 데미지 % (평균 난수 × 명중)
  hitsToKo: number;         // 상대를 쓰러뜨리는 데 필요한 타수 (최대 10)
  speed: number;
  priority: number;         // 최고 기술의 우선도
}

function calcName(name: string): string {
  const sp = PS.Dex.species.get(name);
  if (sp.name === 'Aegislash') return 'Aegislash-Shield';
  if ((gen.species as any).get(PS.toID(sp.name))) return sp.name;
  return sp.baseSpecies;
}

function toCalc(s: MatchupSet): Pokemon {
  let species = s.set.species;
  let ability = s.set.ability;
  if (s.mega) {
    const mega = dex.items.get(s.set.item).megaStone?.[dex.species.get(s.set.species).name];
    if (!mega) throw new Error(`${s.set.species}은(는) ${s.set.item}로 메가진화할 수 없다`);
    species = mega;
    ability = undefined;  // 메가 폼의 특성을 쓴다
  }
  return new Pokemon(gen, calcName(species), { item: s.set.item || undefined, ability, nature: s.set.nature, evs: s.set.evs });
}

// 등장 시 날씨·필드를 까는 특성. 양쪽이 동시에 등장하면 느린 쪽이 나중에 발동해 덮어쓴다.
const WEATHER: Record<string, string> = { drought: 'Sun', drizzle: 'Rain', sandstream: 'Sand', snowwarning: 'Snow',
  orichalcumpulse: 'Sun' };
const TERRAIN: Record<string, string> = { electricsurge: 'Electric', psychicsurge: 'Psychic', grassysurge: 'Grassy',
  mistysurge: 'Misty', hadronengine: 'Electric', seedsower: 'Grassy' };

function fieldFor(a: Pokemon, b: Pokemon): Field {
  const order = [a, b].sort((x, y) => y.stats.spe - x.stats.spe);  // 빠른 쪽 먼저 발동, 느린 쪽이 덮어씀
  let weather: string | undefined;
  let terrain: string | undefined;
  for (const p of order) {
    const ab = PS.toID(p.ability ?? '');
    if (WEATHER[ab]) weather = WEATHER[ab];
    if (TERRAIN[ab]) terrain = TERRAIN[ab];
  }
  return new Field({ weather: weather as any, terrain: terrain as any });
}

function evalSide(att: MatchupSet, def: MatchupSet, a: Pokemon, d: Pokemon, field: Field): SideEval {
  let best = { move: null as string | null, pct: 0, priority: 0 };
  for (const mv of att.set.moves ?? []) {
    const m = dex.moves.get(mv);
    if (!m.exists || m.category === 'Status') continue;
    const move = new Move(gen, m.name);
    let rolls: number[];
    try {
      const r = calculate(gen, a, d, move, field.clone());
      const dmg = r.damage as any;
      rolls = Array.isArray(dmg) ? (Array.isArray(dmg[0]) ? dmg.map((h: number[]) => h[7]) : dmg) : [dmg];
      if (Array.isArray(dmg) && Array.isArray(dmg[0])) rolls = [dmg.reduce((s: number, h: number[]) => s + h[7], 0)];
    } catch {
      continue;
    }
    const mean = rolls.reduce((s, x) => s + x, 0) / rolls.length;
    const acc = m.accuracy === true ? 1 : m.accuracy / 100;
    const pct = (100 * mean * acc) / d.maxHP();
    if (pct > best.pct) best = { move: m.name, pct, priority: m.priority };
  }
  const speed = a.stats.spe * (att.set.item && PS.toID(att.set.item) === 'choicescarf' ? 1.5 : 1);
  return { species: a.name, bestMove: best.move, bestPercent: +best.pct.toFixed(1),
    hitsToKo: best.pct > 0 ? Math.min(10, Math.ceil(100 / best.pct)) : 10, speed: Math.floor(speed), priority: best.priority };
}

export function matchupFast(req: { a: MatchupSet; b: MatchupSet }) {
  const a = toCalc(req.a);
  const b = toCalc(req.b);
  const field = fieldFor(a, b);
  const ea = evalSide(req.a, req.b, a, b, field);
  const eb = evalSide(req.b, req.a, b, a, field.clone().swap());
  // 선공: 우선도가 높은 쪽, 같으면 스피드. 같은 스피드는 0
  const first = ea.priority !== eb.priority ? Math.sign(ea.priority - eb.priority) : Math.sign(ea.speed - eb.speed);
  const diff = (eb.hitsToKo - ea.hitsToKo) + 0.5 * first;
  const score = 1 / (1 + Math.exp(-SCORE_K * diff));
  return { score: +score.toFixed(4), first, weather: field.weather ?? null, terrain: field.terrain ?? null, a: ea, b: eb };
}
