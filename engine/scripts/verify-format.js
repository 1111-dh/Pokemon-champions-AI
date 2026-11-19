// 단계 0 검증: Showdown champions 포맷 규칙과 데이터를 엔진에서 직접 읽는다.
const path = require('path');
const PS = path.resolve(__dirname, '../external/pokemon-showdown/dist/sim');
const { Dex } = require(PS);

const fmt = Dex.formats.get('gen9championsbssregmc');
const rt = Dex.formats.getRuleTable(fmt);
const dex = Dex.forFormat(fmt);
console.log('format:', fmt.id, '| name:', fmt.name, '| exists:', fmt.exists, '| mod:', fmt.mod, '| gameType:', fmt.gameType);
console.log('pickedTeamSize:', rt.pickedTeamSize, '| minTeamSize:', rt.minTeamSize, '| maxTeamSize:', rt.maxTeamSize,
  '| adjustLevel:', rt.adjustLevel, '| evLimit:', rt.evLimit);
console.log('rules:', [...rt.keys()].filter(k => !k.startsWith('-') && !k.startsWith('+') && !k.startsWith('*')).join(', '));
console.log('bans:', [...rt.keys()].filter(k => k.startsWith('-')).join(', '));

// 허용 종: 포맷 검증기로 각 종을 검사하는 대신 isNonstandard / 태그로 1차 집계
const { TeamValidator } = require(PS);
const v = TeamValidator.get(fmt);
const all = dex.species.all();
const allowed = [], megas = [];
for (const s of all) {
  if (!s.exists) continue;
  const banReason = v.checkSpecies({ species: s.name }, s, s, {});
  const nonstd = s.isNonstandard;
  if (!banReason && !nonstd) {
    allowed.push(s.name);
    if (s.isMega) megas.push(s.name);
  }
}
const allowedBase = allowed.filter(n => !dex.species.get(n).isMega);
console.log('allowed species entries (incl. formes, battle-only):', allowed.length, '| non-mega:', allowedBase.length, '| mega:', megas.length);
console.log('megas:', megas.join(', '));

const g = dex.species.get('Garchomp-Mega-Z');
console.log('Garchomp-Mega-Z:', g.types, g.abilities, g.baseStats, 'requiredItem:', g.requiredItem, 'nonstd:', g.isNonstandard);

// 타입 상성: Ice -> Garchomp (Dragon/Ground) 
const target = dex.species.get('Garchomp');
console.log('Ice vs Garchomp typeMod sum:', dex.getEffectiveness('Ice', target.types), '(2 = 4x)');

// 수정된 기술 수
const modMoves = require(path.resolve(__dirname, '../external/pokemon-showdown/dist/data/mods/champions/moves.js')).Moves;
const modAbil = require(path.resolve(__dirname, '../external/pokemon-showdown/dist/data/mods/champions/abilities.js')).Abilities;
const modItems = require(path.resolve(__dirname, '../external/pokemon-showdown/dist/data/mods/champions/items.js')).Items;
console.log('champions mod overrides -> moves:', Object.keys(modMoves).length, '| abilities:', Object.keys(modAbil).length, '| items:', Object.keys(modItems).length);
