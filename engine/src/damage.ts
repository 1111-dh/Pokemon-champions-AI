// /damage: 공격자·방어자 세트와 기술로 데미지 16단계와 급소 값을 계산한다 (PROJECT_PLAN 2절, 9.2절).
// 기준: 배틀 시뮬레이션과 같은 Showdown 엔진(champions 모드). 엔진의 난수 함수를 단계별 고정값으로 바꿔 16개 값을 모두 얻는다.
// 교차 검증: @smogon/calc 0.12의 Champions 계산(gen 0). 두 결과가 다르면 mismatch로 알린다.
import { calculate, Field as CalcField, Generations, Move as CalcMove, Pokemon as CalcPokemon } from '@smogon/calc';
import { PS } from './ps';

const CUSTOM_FORMAT = 'gen9championscustomgame'; // 1:1 배틀을 만들 수 있는 챔피언스 커스텀 포맷 (레벨 50, 같은 능력치 공식)
const calcGen = Generations.get(0 as any);       // @smogon/calc에서 gen 0 = Champions

export interface DamageSide {
  set: any;                 // PokemonSet (species, item, ability, nature, evs=Stat Points, moves 생략 가능)
  mega?: boolean;           // 메가진화한 상태로 계산
  boosts?: Record<string, number>;
  status?: string;          // 'brn' | 'par' | ...
  hpPercent?: number;       // 현재 HP 비율 (분노/역린 같은 HP 의존 기술용, 기본 100)
}

export interface DamageReq {
  attacker: DamageSide;
  defender: DamageSide;
  move: string;
  field?: { weather?: string; terrain?: string; screens?: string[] };   // screens: 맞는 쪽의 벽 (reflect, lightscreen, auroraveil)
  crossCheck?: boolean;     // @smogon/calc 교차 검증 (기본 true)
}

export interface DamageResult {
  move: string;
  attacker: { species: string; stats: Record<string, number> };
  defender: { species: string; maxhp: number; stats: Record<string, number> };
  rolls: number[];          // 난수 16단계 (85%→100% 순서)
  critRolls: number[];
  percent: { min: number; max: number; critMin: number; critMax: number };
  typeEffectiveness: number;   // 0이면 무효
  immune: boolean;
  status: boolean;             // 변화기 (데미지 없음)
  hitOptions: number[];        // 타수 선택지 (연속기 [2,3,4,5], 부자유친이면 [2], 단타 [1])
  perHit: { rolls: number[]; critRolls: number[] }[];   // 타마다 난수 16단계 (트리플악셀처럼 타마다 위력이 다를 수 있다)
  attackerAbility: string;     // 메가 후 실제 특성 (스킬링크 등)
  calc?: { rolls: number[]; critRolls: number[]; mismatch: boolean; error?: string };
}

function battleFor(attacker: DamageSide, defender: DamageSide, field?: DamageReq['field']) {
  const mk = (s: DamageSide) => [{ ...s.set, level: 50, moves: s.set.moves?.length ? s.set.moves : ['splash'] }];
  const battle = new PS.Battle({ formatid: CUSTOM_FORMAT, seed: 'sodium,00000000000000000000000000000000',
    p1: { name: 'a', team: mk(attacker) }, p2: { name: 'd', team: mk(defender) } });
  battle.choose('p1', 'team 1');
  battle.choose('p2', 'team 1');
  const a = battle.p1.active[0];
  const d = battle.p2.active[0];
  const sides = [[attacker, a], [defender, d]] as [DamageSide, any][];
  for (const [side, mon] of sides) {
    if (side.mega) {
      const forme = battle.actions.canMegaEvo(mon);
      if (!forme) throw new Error(`${mon.species.name}은(는) 지닌 도구로 메가진화할 수 없다`);
      battle.actions.runMegaEvo(mon);
    }
  }
  // 등장·메가진화 때 발동한 효과(가뭄 같은 날씨 특성, 위협 등)를 지우고 요청한 상태만 적용한다.
  // 메가진화가 먼저다: 메가리자몽Y의 가뭄은 메가진화할 때 쾌청을 깐다
  battle.field.clearWeather();
  battle.field.clearTerrain();
  for (const mon of [a, d]) mon.clearBoosts();
  for (const [side, mon] of sides) {
    if (side.boosts) mon.setBoost(side.boosts);
    if (side.status) mon.setStatus(side.status);
    if (side.hpPercent !== undefined) mon.sethp(Math.max(1, Math.floor((mon.maxhp * side.hpPercent) / 100)));
  }
  // 날씨·필드는 공격자가 깐 것으로 둔다 (Showdown은 원인 없는 날씨 설정을 막는다)
  if (field?.weather) battle.field.setWeather(field.weather, a, battle.dex.conditions.get(field.weather));
  if (field?.terrain) battle.field.setTerrain(field.terrain, a, battle.dex.conditions.get(field.terrain));
  for (const s of field?.screens ?? []) battle.p2.addSideCondition(s, d);
  return { battle, a, d };
}

function engineRolls(req: DamageReq, crit: boolean, hit = 1) {
  const rolls: number[] = [];
  let ctx: any;
  let typeMod = 0;
  let immune = false;
  let status = false;
  let alwaysCrit = false;
  let multihit: number | number[] | undefined;
  for (let r = 15; r >= 0; r--) {
    ctx = battleFor(req.attacker, req.defender, req.field);
    const { battle, a, d } = ctx;
    const tr = battle.trunc;
    battle.randomizer = (base: number) => tr(tr(base * (100 - r)) / 100);
    let move = battle.dex.getActiveMove(req.move);
    if (!move.exists) throw new Error(`없는 기술: ${req.move}`);
    alwaysCrit = move.willCrit === true;          // 꽃트릭처럼 항상 급소인 기술은 그대로 둔다
    if (!alwaysCrit) move.willCrit = crit;
    move.hit = hit;                                // 몇 번째 타인지 (트리플악셀 위력, 부자유친 2타 보정)
    battle.setActiveMove(move, a, d);
    // 실제 기술 사용(useMove)과 같이 기술을 바꾸는 효과를 먼저 적용한다 (우격다짐, 스킨 특성 등)
    battle.singleEvent('ModifyType', move, null, a, d, move, move);
    battle.singleEvent('ModifyMove', move, null, a, d, move, move);
    move = battle.runEvent('ModifyType', a, d, move, move);
    move = battle.runEvent('ModifyMove', a, d, move, move);
    // 실제 배틀처럼 기술 준비 단계(부자유친의 2타, 변환자재의 타입 변경)와 명중 직전 판정(축전·저수 같은 흡수 특성)을 거친다
    battle.runEvent('PrepareHit', a, d, move);
    multihit = move.multihit;
    status = move.category === 'Status';
    immune = !status && (!d.runImmunity(move, false) || [null, false].includes(battle.runEvent('TryHit', d, a, move)));
    const dmg = status || immune ? 0 : battle.actions.getDamage(a, d, move, true);
    rolls.push(typeof dmg === 'number' ? dmg : 0);
    typeMod = d.runEffectiveness(move);
  }
  return { rolls, ...ctx, typeMod, immune, status, alwaysCrit, multihit } as { rolls: number[]; battle: any; a: any; d: any; typeMod: number;
    immune: boolean; status: boolean; alwaysCrit: boolean; multihit?: number | number[] };
}

function calcRolls(req: DamageReq, crit: boolean): number[] {
  const mk = (s: DamageSide) => {
    const species = s.mega
      ? PS.Dex.forFormat(PS.Dex.formats.get(CUSTOM_FORMAT)).items.get(s.set.item).megaStone?.[PS.Dex.species.get(s.set.species).name]
      : s.set.species;
    // @smogon/calc는 외형만 다른 폼(Florges-Blue 등)을 모르므로 기본 종으로 넘긴다
    const sp = PS.Dex.species.get(species);
    const SPECIAL: Record<string, string> = { Aegislash: 'Aegislash-Shield' };
    const calcName = SPECIAL[sp.name] ?? (sp.cosmeticFormes === undefined && sp.baseSpecies !== sp.name && !sp.isMega && !sp.forme.match(/Alola|Galar|Hisui|Paldea|Mega|Therian|Rotom|Wash|Heat|Frost|Fan|Mow/) && !(calcGen.species.get(PS.toID(sp.name) as any))
      ? sp.baseSpecies : sp.name);
    return new CalcPokemon(calcGen, calcName, {
      item: s.set.item, ability: s.mega ? undefined : s.set.ability, nature: s.set.nature,
      evs: s.set.evs, boosts: s.boosts as any, status: s.status as any,
    });
  };
  const result = calculate(calcGen, mk(req.attacker), mk(req.defender), new CalcMove(calcGen, req.move, { isCrit: crit }),
    new CalcField({ weather: req.field?.weather as any, terrain: req.field?.terrain as any }));
  const d = result.damage as any;
  const arr: number[] = Array.isArray(d) ? (Array.isArray(d[0]) ? d[0] : d) : [d];
  return arr.length === 16 ? arr : new Array(16).fill(arr[0] ?? 0);
}

const pct = (x: number, hp: number) => Math.floor((1000 * x) / hp) / 10;

export function damage(req: DamageReq): DamageResult {
  const normal = engineRolls(req, false);
  const crit = engineRolls(req, true);
  const { a, d } = normal;
  const mh = normal.multihit;
  const hitOptions = Array.isArray(mh) ? Array.from({ length: mh[1] - mh[0] + 1 }, (_, i) => mh[0] + i) : [typeof mh === 'number' ? mh : 1];
  const perHit = [{ rolls: normal.rolls, critRolls: crit.rolls }];
  for (let h = 2; h <= hitOptions[hitOptions.length - 1]; h++) {
    perHit.push({ rolls: engineRolls(req, false, h).rolls, critRolls: engineRolls(req, true, h).rolls });
  }
  const statsOf = (m: any) => ({ hp: m.maxhp, atk: m.storedStats.atk, def: m.storedStats.def,
    spa: m.storedStats.spa, spd: m.storedStats.spd, spe: m.storedStats.spe });
  const res: DamageResult = {
    move: PS.Dex.moves.get(req.move).name,
    attacker: { species: a.species.name, stats: statsOf(a) },
    defender: { species: d.species.name, maxhp: d.maxhp, stats: statsOf(d) },
    rolls: normal.rolls,
    critRolls: crit.rolls,
    percent: { min: pct(normal.rolls[0], d.maxhp), max: pct(normal.rolls[15], d.maxhp),
      critMin: pct(crit.rolls[0], d.maxhp), critMax: pct(crit.rolls[15], d.maxhp) },
    typeEffectiveness: normal.immune ? 0 : 2 ** normal.typeMod,
    immune: normal.immune,
    status: normal.status,
    hitOptions,
    perHit,
    attackerAbility: a.ability,
  };
  if (req.crossCheck !== false && !normal.status) {
    try {
      const c = calcRolls(req, false);
      const cc = calcRolls(req, true);
      res.calc = { rolls: c, critRolls: cc,
        mismatch: c.join() !== res.rolls.join() || cc.join() !== res.critRolls.join() };
    } catch (e: any) {
      res.calc = { rolls: [], critRolls: [], mismatch: true, error: String(e?.message ?? e) };
    }
  }
  return res;
}
