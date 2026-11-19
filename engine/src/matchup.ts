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

// ---------- 날씨·필드 ----------
// 등장 시 날씨·필드를 까는 특성. 양쪽이 동시에 등장하면 느린 쪽이 나중에 발동해 덮어쓴다.
// 지속: 5턴, 대응 도구(바위류, 그라운드코트)면 8턴. 날씨는 턴 1~N의 기술에 적용되고, 날씨 턴 끝 처리(모래바람 피해,
// 건조피부·젖은접시·아이스바디·선파워)는 턴 1~N−1에만 일어난다(N턴 끝에 날씨가 먼저 끝남). 그래스필드 회복은 턴 1~N.
// Showdown champions 모드 실측(docs/VERIFIED.md). 날씨부정·에어록이 있으면 날씨 효과 전체를 끈다(남은 턴은 그대로 흐름).
const WEATHER: Record<string, string> = { drought: 'Sun', drizzle: 'Rain', sandstream: 'Sand', snowwarning: 'Snow',
  orichalcumpulse: 'Sun' };
const TERRAIN: Record<string, string> = { electricsurge: 'Electric', psychicsurge: 'Psychic', grassysurge: 'Grassy',
  mistysurge: 'Misty', hadronengine: 'Electric', seedsower: 'Grassy' };
const WEATHER_ROCK: Record<string, string> = { Sun: 'heatrock', Rain: 'damprock', Sand: 'smoothrock', Snow: 'icyrock' };
const WEATHER_SPEED: Record<string, string> = { chlorophyll: 'Sun', swiftswim: 'Rain', sandrush: 'Sand', slushrush: 'Snow' };
const WEATHER_SUPPRESS = new Set(['cloudnine', 'airlock']);
const BASE_DURATION = 5, EXTENDED_DURATION = 8;

interface FieldPlan { weather?: string; weatherTurns: number; terrain?: string; terrainTurns: number; suppressed: boolean }

function fieldPlan(a: Pokemon, b: Pokemon, items: [string, string]): FieldPlan {
  const order: [Pokemon, string][] = [[a, items[0]], [b, items[1]]];
  order.sort((x, y) => y[0].stats.spe - x[0].stats.spe);  // 빠른 쪽 먼저 발동, 느린 쪽이 덮어씀
  const plan: FieldPlan = { weatherTurns: 0, terrainTurns: 0,
    suppressed: [a, b].some(p => WEATHER_SUPPRESS.has(PS.toID(p.ability ?? ''))) };
  for (const [p, item] of order) {
    const ab = PS.toID(p.ability ?? '');
    if (WEATHER[ab]) {
      plan.weather = WEATHER[ab];
      plan.weatherTurns = item === WEATHER_ROCK[plan.weather] ? EXTENDED_DURATION : BASE_DURATION;
    }
    if (TERRAIN[ab]) {
      plan.terrain = TERRAIN[ab];
      plan.terrainTurns = item === 'terrainextender' ? EXTENDED_DURATION : BASE_DURATION;
    }
  }
  return plan;
}

/** 그 턴의 기술에 적용되는 날씨·필드 (날씨는 억제되면 없는 것으로 본다). */
interface TurnField { weather?: string; terrain?: string; fa: Field; fb: Field }

class FieldTimeline {
  private cache = new Map<string, TurnField>();
  constructor(readonly plan: FieldPlan) {}
  at(turn: number): TurnField {
    const weather = !this.plan.suppressed && turn <= this.plan.weatherTurns ? this.plan.weather : undefined;
    const terrain = turn <= this.plan.terrainTurns ? this.plan.terrain : undefined;
    const key = `${weather}|${terrain}`;
    let f = this.cache.get(key);
    if (!f) {
      const fa = new Field({ weather: weather as any, terrain: terrain as any });
      f = { weather, terrain, fa, fb: fa.clone().swap() };
      this.cache.set(key, f);
    }
    return f;
  }
  /** 턴 끝 날씨 처리가 일어나는 날씨 (N턴 끝에는 날씨가 먼저 끝나서 없다). */
  residualWeather(turn: number): string | undefined {
    return !this.plan.suppressed && turn < this.plan.weatherTurns ? this.plan.weather : undefined;
  }
  residualTerrain(turn: number): string | undefined {
    return turn <= this.plan.terrainTurns ? this.plan.terrain : undefined;
  }
}

// ---------- 턴 단위 HP 경주 ----------
// 서로 매 턴 그 턴의 기대 데미지가 가장 큰 기술을 쓴다고 보고, 양쪽이 각자 기절하는 시각을 계산한다.
// 시각 = 턴 번호 + (먼저 움직이면 0, 나중이면 0.5, 턴 끝 처리는 0.75). 점수 = sigmoid(K × (내 기절 시각 − 상대 기절 시각)).
// 반영: 첫 턴 전용 기술(만나자마자: 풀죽음), 자폭류 제외, 반동, 흡수, 자기 랭크 변화(용성군, 니트로차지 등, 심술꾸러기),
// 반동으로 쉬는 기술, 모으는 기술, 위협, 기합의띠·옹골참, 멀티스케일, 울퉁불퉁멧·까칠한피부·철가시, 생명의구슬,
// 먹다남은음식·검은진흙, 자뭉열매, 구애 계열 고정, 확정 부가효과의 상대 공격·특공·스피드 하락(우격다짐이면 부가효과 없음).
// 날씨·필드(위 FieldTimeline): 턴마다 남은 지속 턴으로 데미지 보정, 날씨 스피드 특성(엽록소·쓱쓱·모래헤치기·눈치우기)과
// 서핑테일, 날씨 명중(번개·폭풍 비 필중·쾌청 50%, 눈보라 눈 필중, 모래숨기·눈숨기 회피), 모으는 기술 생략(쾌청 솔라빔·솔라블레이드,
// 메가솔라, 비 일렉트릭빔), 모래바람 피해, 건조피부·젖은접시·아이스바디·선파워, 그래스필드 회복과 그래스슬라이더 선공,
// 사이코필드의 선공기 차단(땅에 있는 상대). 변화기와 교체는 다루지 않는다(정밀 시뮬레이션 몫).
const MAX_TURNS = 10;
const INTIMIDATE_IMMUNE = new Set(['clearbody', 'hypercutter', 'innerfocus', 'oblivious', 'owntempo', 'scrappy',
  'whitesmoke', 'fullmetalbody']);
const FLINCH_IMMUNE = new Set(['innerfocus', 'shielddust']);
const CONTACT_DAMAGE: Record<string, number> = { roughskin: 1 / 8, ironbarbs: 1 / 8 };
const SAND_IMMUNE_ABILITY = new Set(['sandveil', 'sandrush', 'sandforce', 'overcoat', 'magicguard']);
const SUN_CHARGE = new Set(['solarbeam', 'solarblade']);

interface MoveInfo {
  name: string; id: string; priority: number; category: string; accuracy: number | true;
  recoil: number; drain: number; firstTurnOnly: boolean; recharge: boolean; charge: boolean; contact: boolean;
  selfBoosts: Record<string, number>; targetBoosts: Record<string, number>;
  secondarySelf: boolean;   // 자기 랭크 변화가 부가효과라서 우격다짐이면 사라진다
}

interface Fighter {
  set: any; calc: Pokemon; ability: string; item: string; types: string[]; maxHP: number; grounded: boolean;
  moves: MoveInfo[];
}

function moveInfo(name: string): MoveInfo | null {
  const m = dex.moves.get(name);
  if (!m.exists || m.category === 'Status' || m.selfdestruct || ['finalgambit', 'memento'].includes(m.id)) return null;
  const sb: Record<string, number> = { ...(m.self?.boosts ?? {}) };
  if (m.secondary?.chance === 100 && m.secondary.self?.boosts) Object.assign(sb, m.secondary.self.boosts);
  // 확정(100%) 부가효과로 상대 능력이 떨어지는 기술 (매지컬플레임, 바크아웃, 얼어붙은바람, 암석봉인 등). 공격·특공·스피드만 추적한다.
  const tb: Record<string, number> = m.secondary?.chance === 100 && m.secondary.boosts ? { ...m.secondary.boosts } : {};
  return {
    name: m.name, id: m.id, priority: m.priority, category: m.category, accuracy: m.accuracy,
    recoil: m.recoil ? m.recoil[0] / m.recoil[1] : 0, drain: m.drain ? m.drain[0] / m.drain[1] : 0,
    firstTurnOnly: ['fakeout', 'firstimpression'].includes(m.id), recharge: !!m.flags.recharge,
    charge: !!m.flags.charge, contact: !!m.flags.contact, selfBoosts: sb, targetBoosts: tb,
    secondarySelf: !(m.self?.boosts) && Object.keys(sb).length > 0,
  };
}

function fighter(s: MatchupSet, p: Pokemon): Fighter {
  const ability = PS.toID(p.ability ?? ''), item = PS.toID(s.set.item ?? ''), types = [...p.types] as string[];
  // 풍선은 맞으면 터지지만 데미지 계산(@smogon/calc)이 계속 든 것으로 보므로 여기서도 같게 둔다 (KNOWN_GAPS)
  const grounded = !types.includes('Flying') && ability !== 'levitate' && item !== 'airballoon';
  return { set: s.set, calc: p, ability, item, types, maxHP: p.maxHP(), grounded,
    moves: (s.set.moves ?? []).map(moveInfo).filter((x: MoveInfo | null): x is MoveInfo => !!x) };
}

const stageMul = (s: number) => (s >= 0 ? (2 + s) / 2 : 2 / (2 - s));

/** 날씨를 고려한 명중률 (0~1). 메가솔라는 자기 기술을 쾌청으로 친다. */
function accuracy(mv: MoveInfo, att: Fighter, def: Fighter, weather: string | undefined): number {
  const w = att.ability === 'megasol' ? 'Sun' : weather;
  let acc = mv.accuracy;
  if (['thunder', 'hurricane'].includes(mv.id)) {
    if (w === 'Rain') acc = true;
    else if (w === 'Sun') acc = 50;
  }
  if (mv.id === 'blizzard' && w === 'Snow') acc = true;
  if (acc === true) return 1;
  let a = acc / 100;
  // 모래숨기·눈숨기: 회피 보정 (Showdown onModifyAccuracy ×3277/4096). 상대 기준이라 메가솔라와 무관하게 실제 날씨를 본다.
  if ((def.ability === 'sandveil' && weather === 'Sand') || (def.ability === 'snowcloak' && weather === 'Snow')) a *= 3277 / 4096;
  return a;
}

class DamageTable {
  private cache = new Map<string, number>();
  constructor(private att: Fighter, private def: Fighter, private baseBoosts: Record<string, number>, private side: 'a' | 'b') {}
  /** 기대 데미지 (상대 최대 HP 대비 %, 평균 난수 × 명중). */
  pct(mv: MoveInfo, atk: number, spa: number, defFull: boolean, tf: TurnField): number {
    const multiscale = ['multiscale', 'shadowshield'].includes(this.def.ability);
    const key = `${mv.id}|${atk}|${spa}|${multiscale && defFull ? 1 : 0}|${tf.weather}|${tf.terrain}`;
    const hit = this.cache.get(key);
    if (hit !== undefined) return hit;
    let pct = 0;
    try {
      const a = this.att.calc.clone();
      a.boosts.atk = Math.max(-6, Math.min(6, atk + (this.baseBoosts.atk ?? 0)));
      a.boosts.spa = Math.max(-6, Math.min(6, spa + (this.baseBoosts.spa ?? 0)));
      const d = this.def.calc.clone();
      if (multiscale && !defFull) d.originalCurHP = Math.max(1, d.maxHP() - 1);
      const r = calculate(gen, a, d, new Move(gen, mv.name), (this.side === 'a' ? tf.fa : tf.fb).clone());
      const dmg = r.damage as any;
      let mean: number;
      if (Array.isArray(dmg) && Array.isArray(dmg[0])) mean = dmg.reduce((t: number, h: number[]) => t + h[7], 0);
      else if (Array.isArray(dmg)) mean = dmg.reduce((t: number, x: number) => t + x, 0) / dmg.length;
      else mean = dmg;
      pct = (100 * mean * accuracy(mv, this.att, this.def, tf.weather)) / this.def.maxHP;
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

function speedOf(s: RaceSide, tf: TurnField): number {
  const ab = s.f.ability;
  const weatherSpeed = WEATHER_SPEED[ab] !== undefined && WEATHER_SPEED[ab] === tf.weather;
  const surge = ab === 'surgesurfer' && tf.terrain === 'Electric';
  return s.f.calc.stats.spe * stageMul(s.stages.spe) * (s.f.item === 'choicescarf' ? 1.5 : 1) * (weatherSpeed || surge ? 2 : 1);
}

/** 그래스필드에서 땅에 있는 사용자의 그래스슬라이더는 우선도 +1. */
function priorityOf(s: RaceSide, mv: MoveInfo | null, tf: TurnField): number {
  if (!mv) return 0;
  if (mv.id === 'grassyglide' && tf.terrain === 'Grassy' && s.f.grounded) return mv.priority + 1;
  return mv.priority;
}

/** 사이코필드: 땅에 있는 대상에게 쓰는 선공기(우선도 > 0)는 실패한다. */
function blockedByTerrain(att: RaceSide, def: RaceSide, mv: MoveInfo, tf: TurnField): boolean {
  return tf.terrain === 'Psychic' && def.f.grounded && priorityOf(att, mv, tf) > 0;
}

function skipsCharge(att: RaceSide, mv: MoveInfo, tf: TurnField): boolean {
  if (att.f.item === 'powerherb') return true;
  const sun = tf.weather === 'Sun' || att.f.ability === 'megasol';
  return (SUN_CHARGE.has(mv.id) && sun) || (mv.id === 'electroshot' && tf.weather === 'Rain');
}

function choose(me: RaceSide, foe: RaceSide, turn: number, tf: TurnField): MoveInfo | null {
  if (me.recharging) return null;
  if (me.charging) return me.charging;
  if (me.lock) return me.lock;
  const choice = ['choicescarf', 'choiceband', 'choicespecs'].includes(me.f.item);
  let best: MoveInfo | null = null, bestV = 0;
  for (const mv of me.f.moves) {
    if (mv.firstTurnOnly && (turn > 1 || choice)) continue;
    if (blockedByTerrain(me, foe, mv, tf)) continue;
    let v = me.table.pct(mv, me.stages.atk, me.stages.spa, foe.hp >= 100, tf);
    if (mv.recharge || (mv.charge && !skipsCharge(me, mv, tf))) v /= 2;
    if (mv.firstTurnOnly && v > 0) v += 1000;   // 첫 턴 만나자마자는 (먼저 움직이면) 상대 행동을 막으므로 우선한다
    if (v > bestV) { best = mv; bestV = v; }
  }
  if (best && choice) me.lock = best;
  return best;
}

const DROP_IMMUNE = new Set(['clearbody', 'whitesmoke', 'fullmetalbody']);
const SECONDARY_IMMUNE_ABILITY = new Set(['shielddust']);

function boost(s: RaceSide, boosts: Record<string, number>) {
  const sign = s.f.ability === 'contrary' ? -1 : 1;
  for (const [st, n] of Object.entries(boosts)) {
    if (st in s.stages) s.stages[st] = Math.max(-6, Math.min(6, s.stages[st] + sign * (n as number)));
  }
}

/** 부가효과로 상대 능력을 떨어뜨린다 (인분·비밀망토는 부가효과 차단, 클리어바디류·클리어참은 하락 차단, 미러아머는 반사,
 *  오기·승기는 하락할 때 공격·특공 +2). */
function dropTarget(att: RaceSide, def: RaceSide, boosts: Record<string, number>) {
  const tracked = Object.entries(boosts).filter(([st, n]) => st in def.stages && (n as number) < 0);
  if (!tracked.length) return;
  if (SECONDARY_IMMUNE_ABILITY.has(def.f.ability) || def.f.item === 'covertcloak') return;
  if (DROP_IMMUNE.has(def.f.ability) || def.f.item === 'clearamulet') return;
  if (def.f.ability === 'mirrorarmor') { boost(att, Object.fromEntries(tracked)); return; }
  boost(def, Object.fromEntries(tracked));
  if (def.f.ability === 'defiant') boost(def, { atk: 2 });
  if (def.f.ability === 'competitive') boost(def, { spa: 2 });
}

/** 기술을 쓴다. 상대에게 맞았으면 true. */
function hit(att: RaceSide, def: RaceSide, mv: MoveInfo, time: number, tf: TurnField): boolean {
  if (mv.charge && !skipsCharge(att, mv, tf) && att.charging !== mv) { att.charging = mv; return false; }
  att.charging = null;
  if (blockedByTerrain(att, def, mv, tf)) return false;
  const full = def.hp >= 100;
  let dmg = att.table.pct(mv, att.stages.atk, att.stages.spa, full, tf);
  if (dmg <= 0) return false;
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
  const sheerForce = att.f.ability === 'sheerforce';
  if (!(sheerForce && mv.secondarySelf)) boost(att, mv.selfBoosts);
  if (!sheerForce && def.hp > 0) dropTarget(att, def, mv.targetBoosts);
  if (mv.recharge) att.recharging = true;
  if (def.hp <= 0 && def.death > MAX_TURNS) def.death = time;
  if (att.hp <= 0 && att.death > MAX_TURNS) att.death = time;
  return true;
}

function endOfTurn(s: RaceSide, turn: number, fields: FieldTimeline) {
  if (s.hp <= 0) return;
  const w = fields.residualWeather(turn);
  const magicGuard = s.f.ability === 'magicguard';
  if (w === 'Sand' && !magicGuard && s.f.item !== 'safetygoggles'
      && !SAND_IMMUNE_ABILITY.has(s.f.ability) && !s.f.types.some(t => ['Rock', 'Ground', 'Steel'].includes(t))) s.hp -= 6.25;
  if (s.f.ability === 'dryskin') {
    if (w === 'Rain') s.hp = Math.min(100, s.hp + 12.5);
    else if (w === 'Sun' && !magicGuard) s.hp -= 12.5;
  }
  if (s.f.ability === 'raindish' && w === 'Rain') s.hp = Math.min(100, s.hp + 6.25);
  if (s.f.ability === 'icebody' && w === 'Snow') s.hp = Math.min(100, s.hp + 6.25);
  if (s.f.ability === 'solarpower' && w === 'Sun' && !magicGuard) s.hp -= 12.5;
  if (s.hp > 0 && fields.residualTerrain(turn) === 'Grassy' && s.f.grounded) s.hp = Math.min(100, s.hp + 6.25);
  if (s.hp > 0 && (s.f.item === 'leftovers' || (s.f.item === 'blacksludge' && s.f.types.includes('Poison')))) s.hp = Math.min(100, s.hp + 6.25);
  if (s.hp <= 0 && s.death > MAX_TURNS) s.death = turn + 0.75;
}

/** tie: 같은 스피드일 때 A가 먼저(1) / B가 먼저(-1). */
function race(A: Fighter, B: Fighter, ta: DamageTable, tb: DamageTable, fields: FieldTimeline,
              boostsA: Record<string, number>, boostsB: Record<string, number>, tie: 1 | -1) {
  const a = newSide(A, ta, boostsA);
  const b = newSide(B, tb, boostsB);
  let tied = false;
  let firstOrder = 0;
  for (let turn = 1; turn <= MAX_TURNS; turn++) {
    const tf = fields.at(turn);
    const ma = a.recharging ? null : choose(a, b, turn, tf);
    const mb = b.recharging ? null : choose(b, a, turn, tf);
    const pa = priorityOf(a, ma, tf), pb = priorityOf(b, mb, tf);
    const sa = speedOf(a, tf), sb = speedOf(b, tf);
    if (turn === 1) {
      a.firstMove = ma; b.firstMove = mb;
      firstOrder = pa !== pb ? Math.sign(pa - pb) : Math.sign(sa - sb);
    }
    if (pa === pb && sa === sb) tied = true;
    const aFirst = pa !== pb ? pa > pb : sa !== sb ? sa > sb : tie === 1;
    const order: [RaceSide, RaceSide, MoveInfo | null][] = aFirst ? [[a, b, ma], [b, a, mb]] : [[b, a, mb], [a, b, ma]];
    let flinched = false;
    order.forEach(([att, def, mv], i) => {
      const wasRecharging = att.recharging;
      if (wasRecharging) { att.recharging = false; return; }
      if (!mv || (i === 1 && flinched)) return;
      const landed = hit(att, def, mv, turn + 0.5 * i, tf);
      if (landed && i === 0 && mv.firstTurnOnly && !FLINCH_IMMUNE.has(def.f.ability) && def.f.item !== 'covertcloak') flinched = true;
    });
    endOfTurn(a, turn, fields);
    endOfTurn(b, turn, fields);
    if (a.death <= MAX_TURNS && b.death <= MAX_TURNS) break;
  }
  return { a, b, tied, aFirst: firstOrder };
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

function sideSummary(s: RaceSide, tf: TurnField): SideEval {
  const first = s.firstMove;
  const pct = first ? s.table.pct(first, 0, 0, true, tf) : 0;
  return { species: s.f.calc.name, bestMove: first?.name ?? null, bestPercent: +pct.toFixed(1),
    hitsToKo: pct > 0 ? Math.min(10, Math.ceil(100 / pct)) : 10, speed: Math.floor(speedOf(s, tf)),
    priority: priorityOf(s, first, tf), faintsAt: s.death > MAX_TURNS ? null : s.death } as SideEval;
}

export function matchupFast(req: { a: MatchupSet; b: MatchupSet }) {
  const pa = toCalc(req.a);
  const pb = toCalc(req.b);
  const A = fighter(req.a, pa), B = fighter(req.b, pb);
  const fields = new FieldTimeline(fieldPlan(pa, pb, [A.item, B.item]));
  const boostsA: Record<string, number> = {}, boostsB: Record<string, number> = {};
  intimidate(A, B, boostsB, boostsA);
  intimidate(B, A, boostsA, boostsB);
  const ta = new DamageTable(A, B, boostsA, 'a'), tb = new DamageTable(B, A, boostsB, 'b');
  const runs = [race(A, B, ta, tb, fields, boostsA, boostsB, 1)];
  const r0 = runs[0];
  // 같은 우선도·스피드로 순서가 갈린 턴이 있었으면 반대 순서도 계산해 평균한다 (대칭 유지)
  if (r0.tied) runs.push(race(A, B, ta, tb, fields, boostsA, boostsB, -1));
  const score = runs.reduce((t, r) => t + 1 / (1 + Math.exp(-SCORE_K * (r.a.death - r.b.death))), 0) / runs.length;
  const plan = fields.plan, tf1 = fields.at(1);
  return { score: +score.toFixed(4), first: r0.aFirst,
    weather: plan.weather ?? null, weatherTurns: plan.weather ? plan.weatherTurns : 0, weatherSuppressed: plan.suppressed,
    terrain: plan.terrain ?? null, terrainTurns: plan.terrain ? plan.terrainTurns : 0,
    a: sideSummary(r0.a, tf1), b: sideSummary(r0.b, tf1) };
}
