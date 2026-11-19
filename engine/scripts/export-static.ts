// Showdown champions 모드의 정적 데이터를 JSON으로 내보낸다. Python의 update-static이 이 출력을 읽는다.
// 사용법: npx tsx scripts/export-static.ts > out.json
// 내보내는 범위: 포맷 검증기를 통과하는 모든 종(메가 폼 포함)과, 그 종들이 배울 수 있는 기술, 쓸 수 있는 도구와 특성.
// 공식 허용 목록으로 거르는 일은 Python 쪽에서 한다(PROJECT_PLAN 4.1).
import { execSync } from 'node:child_process';
import path from 'node:path';
import { PS, FORMAT_ID } from '../src/ps';

const format = PS.Dex.formats.get(FORMAT_ID);
const dex = PS.Dex.forFormat(format);
const validator = PS.TeamValidator.get(FORMAT_ID);
const psDir = path.resolve(__dirname, '../external/pokemon-showdown');
const commit = execSync('git rev-parse HEAD', { cwd: psDir }).toString().trim();

const legal = (s: any) => s.exists && !s.isNonstandard && !validator.checkSpecies({ species: s.name }, s, s, {});

const species: any[] = [];
const learnsets: Record<string, string[]> = {};
const usedMoves = new Set<string>();
const usedAbilities = new Set<string>();
const allMoves = dex.moves.all().filter((m: any) => m.exists && !m.isNonstandard && !m.isZ && !m.isMax);

for (const s of dex.species.all()) {
  if (!legal(s)) continue;
  const base = dex.species.get(s.baseSpecies);
  species.push({
    id: s.id,
    name: s.name,
    num: s.num,
    base_species: s.baseSpecies,
    forme: s.forme || '',
    form_index: (base.formeOrder ?? [base.name]).indexOf(s.name),
    is_mega: !!s.isMega,
    battle_only: s.battleOnly ?? null,
    required_item: s.requiredItem ?? null,
    types: s.types,
    base_stats: s.baseStats,
    abilities: s.abilities,
    weightkg: s.weightkg,
  });
  for (const a of Object.values(s.abilities) as string[]) usedAbilities.add(PS.toID(a));
  if (s.isMega) continue; // 메가 폼은 기본 폼의 기술을 쓴다
  const learnable: string[] = [];
  for (const m of allMoves) {
    if (!validator.checkCanLearn(m, s)) learnable.push(m.id);
  }
  learnsets[s.id] = learnable;
  for (const m of learnable) usedMoves.add(m);
}

const moves = allMoves
  .filter((m: any) => usedMoves.has(m.id))
  .map((m: any) => ({
    id: m.id,
    name: m.name,
    num: m.num,
    type: m.type,
    category: m.category,
    base_power: m.basePower,
    accuracy: m.accuracy === true ? null : m.accuracy,
    pp: m.pp,
    priority: m.priority,
    target: m.target,
    flags: Object.keys(m.flags ?? {}),
    has_secondary: !!(m.secondary || m.secondaries),
    desc: m.shortDesc || m.desc || '',
  }));

const items = dex.items
  .all()
  .filter((i: any) => i.exists && !i.isNonstandard)
  .map((i: any) => ({
    id: i.id,
    name: i.name,
    num: i.num,
    mega_stone: i.megaStone ?? null, // { 기본종: 메가폼 }
    is_berry: !!i.isBerry,
    desc: i.shortDesc || i.desc || '',
  }));

const abilities = dex.abilities
  .all()
  .filter((a: any) => a.exists && usedAbilities.has(a.id))
  .map((a: any) => ({ id: a.id, name: a.name, num: a.num, desc: a.shortDesc || a.desc || '' }));

const megaForms: any[] = [];
for (const it of items) {
  for (const [baseName, megaName] of Object.entries(it.mega_stone ?? {})) {
    const mega = dex.species.get(megaName as string);
    if (!legal(mega)) continue;
    megaForms.push({ base_species: dex.species.get(baseName).id, mega_species: mega.id, stone_item: it.id });
  }
}

process.stdout.write(JSON.stringify({
  source: 'pokemon-showdown',
  showdown_commit: commit,
  format: FORMAT_ID,
  mod: format.mod,
  exported_at: new Date().toISOString(),
  species,
  moves,
  items,
  abilities,
  learnsets,
  mega_forms: megaForms,
}));
