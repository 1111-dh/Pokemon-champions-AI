// 1:1 상성 빠른 근사 (PROJECT_PLAN 6절). 턴 단위 HP 경주(아래)로 양쪽이 기절하는 시각을 구해 0~1 점수를 낸다. 대량 계산용이라 @smogon/calc(엔진과 교차 검증 97.9% 일치)를 쓴다.
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
  priority: number;         // 첫 턴 기술의 우선도
  faintsAt?: number | null; // HP 경주에서 기절하는 시각 (턴 + 순서), 버티면 null
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

// ---------- 턴 단위 HP 경주 ----------
// 서로 매 턴 그 턴의 기대 데미지가 가장 큰 기술을 쓴다고 보고, 양쪽이 각자 기절하는 시각을 계산한다.
// 시각 = 턴 번호 + (먼저 움직이면 0, 나중이면 0.5, 턴 끝 처리는 0.75). 점수 = sigmoid(K × (내 기절 시각 − 상대 기절 시각)).
// 반영: 첫 턴 전용 기술(만나자마자: 풀죽음), 자폭류 제외, 반동, 흡수, 자기 랭크 변화(용성군, 니트로차지 등, 심술꾸러기),
// 반동으로 쉬는 기술, 모으는 기술, 위협, 기합의띠·옹골참, 멀티스케일, 울퉁불퉁멧·까칠한피부·철가시, 생명의구슬,
// 먹다남은음식·검은진흙, 자뭉열매, 모래바람 피해, 구애 계열 고정. 변화기와 교체는 다루지 않는다(정밀 시뮬레이션 몫).
const MAX_TURNS = 10;
const INTIMIDATE_IMMUNE = new Set(['clearbody', 'hypercutter', 'innerfocus', 'oblivious', 'owntempo', 'scrappy',
  'whitesmoke', 'fullmetalbody']);
const FLINCH_IMMUNE = new Set(['innerfocus', 'shielddust']);
const CONTACT_DAMAGE: Record<string, number> = { roughskin: 1 / 8, ironbarbs: 1 / 8 };
const SAND_IMMUNE_ABILITY = new Set(['sandveil', 'sandrush', 'sandforce', 'overcoat', 'magicguard']);

interface MoveInfo {
  name: string; id: string; priority: number; category: string;
  recoil: number; drain: number; firstTurnOnly: boolean; recharge: boolean; charge: boolean; contact: boolean;
  selfBoosts: Record<string, number>;
}

interface Fighter {
  set: any; calc: Pokemon; ability: string; item: string; types: string[]; maxHP: number;
  moves: MoveInfo[];
}

function moveInfo(name: string): MoveInfo | null {
  const m = dex.moves.get(name);
  if (!m.exists || m.category === 'Status' || m.selfdestruct || ['finalgambit', 'memento'].includes(m.id)) return null;
  const sb: Record<string, number> = { ...(m.self?.boosts ?? {}) };
  if (m.secondary?.chance === 100 && m.secondary.self?.boosts) Object.assign(sb, m.secondary.self.boosts);
  return {
    name: m.name, id: m.id, priority: m.priority, category: m.category,
    recoil: m.recoil ? m.recoil[0] / m.recoil[1] : 0, drain: m.drain ? m.drain[0] / m.drain[1] : 0,
    firstTurnOnly: ['fakeout', 'firstimpression'].includes(m.id), recharge: !!m.flags.recharge,
    charge: !!m.flags.charge, contact: !!m.flags.contact, selfBoosts: sb,
  };
}

function fighter(s: MatchupSet, p: Pokemon): Fighter {
  return { set: s.set, calc: p, ability: PS.toID(p.ability ?? ''), item: PS.toID(s.set.item ?? ''),
    types: [...p.types] as string[], maxHP: p.maxHP(),
    moves: (s.set.moves ?? []).map(moveInfo).filter((x: MoveInfo | null): x is MoveInfo => !!x) };
}

const stageMul = (s: number) => (s >= 0 ? (2 + s) / 2 : 2 / (2 - s));

class DamageTable {
  private cache = new Map<string, number>();
  constructor(private att: Fighter, private def: Fighter, private field: Field, private baseBoosts: Record<string, number>) {}
  /** 기대 데미지 (상대 최대 HP 대비 %, 평균 난수 × 명중). */
  pct(mv: MoveInfo, atk: number, spa: number, defFull: boolean): number {
    const multiscale = ['multiscale', 'shadowshield'].includes(this.def.ability);
    const key = `${mv.id}|${atk}|${spa}|${multiscale && defFull ? 1 : 0}`;
    const hit = this.cache.get(key);
    if (hit !== undefined) return hit;
    let pct = 0;
    try {
      const a = this.att.calc.clone();
      a.boosts.atk = Math.max(-6, Math.min(6, atk + (this.baseBoosts.atk ?? 0)));
      a.boosts.spa = Math.max(-6, Math.min(6, spa + (this.baseBoosts.spa ?? 0)));
      const d = this.def.calc.clone();
      if (multiscale && !defFull) d.originalCurHP = Math.max(1, d.maxHP() - 1);
      const r = calculate(gen, a, d, new Move(gen, mv.name), this.field.clone());
      const dmg = r.damage as any;
      let mean: number;
      if (Array.isArray(dmg) && Array.isArray(dmg[0])) mean = dmg.reduce((t: number, h: number[]) => t + h[7], 0);
      else if (Array.isArray(dmg)) mean = dmg.reduce((t: number, x: number) => t + x, 0) / dmg.length;
      else mean = dmg;
      const m = dex.moves.get(mv.id);
      const acc = m.accuracy === true ? 1 : m.accuracy / 100;
      pct = (100 * mean * acc) / this.def.maxHP;
    } catch { pct = 0; }
    this.cache.set(key, pct);
    return pct;
  }
}

interface RaceSide {
  f: Fighter; table: DamageTable; hp: number; stages: Record<string, number>; sash: boolean; sitrus: boolean;
  lock: MoveInfo | null; recharging: boolean; charging: MoveInfo | null; death: number; firstMove: MoveInfo | null;
}

function newSide(f: Fighter, table: DamageTable, start: Record<string, number>): RaceSide {
  return { f, table, hp: 100, stages: { atk: 0, spa: 0, spe: start.spe ?? 0 }, sash: f.item === 'focussash', sitrus: f.item === 'sitrusberry',
    lock: null, recharging: false, charging: null, death: MAX_TURNS + 1, firstMove: null };
}

function speedOf(s: RaceSide): number {
  return s.f.calc.stats.spe * stageMul(s.stages.spe) * (s.f.item === 'choicescarf' ? 1.5 : 1);
}

function choose(me: RaceSide, foe: RaceSide, turn: number, field: Field): MoveInfo | null {
  if (me.recharging) return null;
  if (me.charging) return me.charging;
  if (me.lock) return me.lock;
  const choice = ['choicescarf', 'choiceband', 'choicespecs'].includes(me.f.item);
  let best: MoveInfo | null = null, bestV = 0;
  for (const mv of me.f.moves) {
    if (mv.firstTurnOnly && (turn > 1 || choice)) continue;
    let v = me.table.pct(mv, me.stages.atk, me.stages.spa, foe.hp >= 100);
    const sunCharge = ['solarbeam', 'solarblade'].includes(mv.id) && field.weather === 'Sun';
    if (mv.recharge || (mv.charge && !sunCharge && me.f.item !== 'powerherb')) v /= 2;
    if (mv.firstTurnOnly && v > 0) v += 1000;   // 첫 턴 만나자마자는 (먼저 움직이면) 상대 행동을 막으므로 우선한다
    if (v > bestV) { best = mv; bestV = v; }
  }
  if (best && choice) me.lock = best;
  return best;
}

function hit(att: RaceSide, def: RaceSide, mv: MoveInfo, time: number, field: Field) {
  const sunCharge = ['solarbeam', 'solarblade'].includes(mv.id) && field.weather === 'Sun';
  if (mv.charge && !sunCharge && att.f.item !== 'powerherb' && att.charging !== mv) { att.charging = mv; return; }
  att.charging = null;
  const full = def.hp >= 100;
  let dmg = att.table.pct(mv, att.stages.atk, att.stages.spa, full);
  if (dmg <= 0) return;
  if (dmg >= def.hp && full && (def.sash || def.f.ability === 'sturdy')) { dmg = def.hp - 0.5; def.sash = false; }
  const dealtHP = Math.min(dmg, def.hp) * def.f.maxHP / 100;
  def.hp -= dmg;
  if (def.sitrus && def.hp > 0 && def.hp <= 50) { def.hp += 25; def.sitrus = false; }
  const noIndirect = att.f.ability === 'magicguard';
  if (mv.recoil && !noIndirect && att.f.ability !== 'rockhead') att.hp -= 100 * mv.recoil * dealtHP / att.f.maxHP;
  if (mv.drain) att.hp = Math.min(100, att.hp + 100 * mv.drain * dealtHP / att.f.maxHP);
  if (att.f.item === 'lifeorb' && !noIndirect) att.hp -= 10;
  if (mv.contact && !noIndirect && att.f.ability !== 'longreach' && att.f.item !== 'protectivepads') {
    if (def.f.item === 'rockyhelmet') att.hp -= 100 / 6;
    if (CONTACT_DAMAGE[def.f.ability]) att.hp -= 100 * CONTACT_DAMAGE[def.f.ability];
  }
  const contrary = att.f.ability === 'contrary' ? -1 : 1;
  for (const [st, n] of Object.entries(mv.selfBoosts)) {
    if (st in att.stages) att.stages[st] = Math.max(-6, Math.min(6, att.stages[st] + contrary * (n as number)));
  }
  if (mv.recharge) att.recharging = true;
  if (def.hp <= 0 && def.death > MAX_TURNS) def.death = time;
  if (att.hp <= 0 && att.death > MAX_TURNS) att.death = time;
}

function endOfTurn(s: RaceSide, turn: number, field: Field) {
  if (s.f.ability !== 'magicguard' && field.weather === 'Sand' && s.f.item !== 'safetygoggles'
      && !SAND_IMMUNE_ABILITY.has(s.f.ability) && !s.f.types.some(t => ['Rock', 'Ground', 'Steel'].includes(t))) s.hp -= 6.25;
  if (s.f.item === 'leftovers' || (s.f.item === 'blacksludge' && s.f.types.includes('Poison'))) s.hp = Math.min(100, s.hp + 6.25);
  if (s.hp <= 0 && s.death > MAX_TURNS) s.death = turn + 0.75;
}

/** tie: 같은 스피드일 때 A가 먼저(1) / B가 먼저(-1). */
function race(A: Fighter, B: Fighter, ta: DamageTable, tb: DamageTable, field: Field, fieldB: Field,
              boostsA: Record<string, number>, boostsB: Record<string, number>, tie: 1 | -1) {
  const a = newSide(A, ta, boostsA);
  const b = newSide(B, tb, boostsB);
  let tied = false;
  const firstSpeeds = [speedOf(a), speedOf(b)];
  for (let turn = 1; turn <= MAX_TURNS; turn++) {
    const ma = a.recharging ? null : choose(a, b, turn, field);
    const mb = b.recharging ? null : choose(b, a, turn, fieldB);
    if (turn === 1) { a.firstMove = ma; b.firstMove = mb; }
    const pa = ma?.priority ?? 0, pb = mb?.priority ?? 0;
    const sa = speedOf(a), sb = speedOf(b);
    if (pa === pb && sa === sb) tied = true;
    const aFirst = pa !== pb ? pa > pb : sa !== sb ? sa > sb : tie === 1;
    const order: [RaceSide, RaceSide, MoveInfo | null][] = aFirst ? [[a, b, ma], [b, a, mb]] : [[b, a, mb], [a, b, ma]];
    let flinched = false;
    order.forEach(([att, def, mv], i) => {
      const wasRecharging = att.recharging;
      if (wasRecharging) { att.recharging = false; return; }
      if (!mv || (i === 1 && flinched)) return;
      const f = att === a ? field : fieldB;
      hit(att, def, mv, turn + 0.5 * i, f);
      if (i === 0 && mv.firstTurnOnly && !FLINCH_IMMUNE.has(def.f.ability) && def.f.item !== 'covertcloak') flinched = true;
    });
    endOfTurn(a, turn, field);
    endOfTurn(b, turn, field);
    if (a.death <= MAX_TURNS && b.death <= MAX_TURNS) break;
  }
  const pa = a.firstMove?.priority ?? 0, pb = b.firstMove?.priority ?? 0;
  return { a, b, tied, aFirst: pa !== pb ? Math.sign(pa - pb) : Math.sign(firstSpeeds[0] - firstSpeeds[1]) };
}

function intimidate(src: Fighter, target: Fighter, out: Record<string, number>, srcOut: Record<string, number>) {
  if (src.ability !== 'intimidate' || INTIMIDATE_IMMUNE.has(target.ability) || target.item === 'clearamulet') return;
  if (target.ability === 'mirrorarmor') { srcOut.atk = (srcOut.atk ?? 0) - 1; return; }
  if (target.ability === 'guarddog') { out.atk = (out.atk ?? 0) + 1; return; }
  out.atk = (out.atk ?? 0) - 1;
  if (target.ability === 'defiant') out.atk += 2;
  if (target.ability === 'competitive') out.spa = (out.spa ?? 0) + 2;
  if (target.ability === 'rattled') out.spe = (out.spe ?? 0) + 1;
}

function sideSummary(s: RaceSide): SideEval {
  const first = s.firstMove;
  const pct = first ? s.table.pct(first, 0, 0, true) : 0;
  return { species: s.f.calc.name, bestMove: first?.name ?? null, bestPercent: +pct.toFixed(1),
    hitsToKo: pct > 0 ? Math.min(10, Math.ceil(100 / pct)) : 10, speed: Math.floor(speedOf(s)), priority: first?.priority ?? 0,
    faintsAt: s.death > MAX_TURNS ? null : s.death } as SideEval;
}

export function matchupFast(req: { a: MatchupSet; b: MatchupSet }) {
  const pa = toCalc(req.a);
  const pb = toCalc(req.b);
  const field = fieldFor(pa, pb);
  const A = fighter(req.a, pa), B = fighter(req.b, pb);
  const boostsA: Record<string, number> = {}, boostsB: Record<string, number> = {};
  intimidate(A, B, boostsB, boostsA);
  intimidate(B, A, boostsA, boostsB);
  const fieldB = field.clone().swap();
  const ta = new DamageTable(A, B, field, boostsA), tb = new DamageTable(B, A, fieldB, boostsB);
  const runs = [race(A, B, ta, tb, field, fieldB, boostsA, boostsB, 1)];
  const r0 = runs[0];
  // 같은 우선도·스피드로 순서가 갈린 턴이 있었으면 반대 순서도 계산해 평균한다 (대칭 유지)
  if (r0.tied) runs.push(race(A, B, ta, tb, field, fieldB, boostsA, boostsB, -1));
  const score = runs.reduce((t, r) => t + 1 / (1 + Math.exp(-SCORE_K * (r.a.death - r.b.death))), 0) / runs.length;
  return { score: +score.toFixed(4), first: r0.aFirst, weather: field.weather ?? null, terrain: field.terrain ?? null,
    a: sideSummary(r0.a), b: sideSummary(r0.b) };
}
