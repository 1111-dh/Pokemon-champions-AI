// 테스트용 합법 팀 생성: 챔피언스 랜덤배틀 생성기로 6마리를 뽑고, BSS 규칙(아이템 중복 금지 등)에 맞게 고친 뒤
// 팀 검증기를 통과한 팀만 돌려준다. 검증기는 세트를 정규화한다(예: adjustLevel = 50).
import { PS, FORMAT_ID, toPsSeed } from './ps';

const FALLBACK_ITEMS = [
  'Sitrus Berry', 'Leftovers', 'Life Orb', 'Focus Sash', 'Choice Scarf', 'Choice Band', 'Choice Specs',
  'Lum Berry', 'Rocky Helmet', 'Assault Vest', 'Expert Belt', 'Shell Bell', 'Black Sludge', 'Bright Powder',
  'Quick Claw', 'Kings Rock', 'Scope Lens', 'Wide Lens', 'Muscle Band', 'Wise Glasses',
];

const validator = PS.TeamValidator.get(FORMAT_ID);
const dex = PS.Dex.forFormat(PS.Dex.formats.get(FORMAT_ID));

function dedupeItems(team: any[]) {
  const used = new Set<string>();
  for (const set of team) {
    const id = PS.toID(set.item);
    if (id && !used.has(id)) { used.add(id); continue; }
    const alt = FALLBACK_ITEMS.find(name => {
      const it = dex.items.get(name);
      return it.exists && !it.isNonstandard && !used.has(it.id);
    });
    set.item = alt ?? '';
    if (alt) used.add(PS.toID(alt));
  }
}

/** 시드에서 결정적으로 합법 팀 하나를 만든다. 실패하면 시드를 바꿔 다시 시도한다. */
export function legalRandomTeam(seed: string, maxTries = 50): { team: any[]; tries: number } {
  for (let k = 0; k < maxTries; k++) {
    const team = PS.Teams.generate('gen9championsrandombattle', { seed: toPsSeed(`${seed}#${k}`) });
    dedupeItems(team);
    const problems = validator.validateTeam(team);
    if (!problems) return { team, tries: k + 1 };
  }
  throw new Error(`합법 팀 생성 실패: ${seed}`);
}

export function validateTeam(team: any[]): string[] | null {
  return validator.validateTeam(team);
}
