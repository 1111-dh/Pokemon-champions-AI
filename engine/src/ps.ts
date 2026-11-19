// Showdown 빌드 결과(dist)를 불러오는 단일 진입점. 타입 정의가 없으므로 any로 다룬다.
import { createHash } from 'node:crypto';
import path from 'node:path';

const PS_DIST = path.resolve(__dirname, '../external/pokemon-showdown/dist/sim');
// eslint-disable-next-line @typescript-eslint/no-require-imports
export const PS: any = require(PS_DIST);

/** 프로젝트 기준 포맷: 챔피언스 싱글 BSS 레귤레이션 M-C (docs/VERIFIED.md 1번). */
export const FORMAT_ID = 'gen9championsbssregmc';

/** 사람이 읽는 문자열 시드에서 Showdown sodium 시드를 결정적으로 만든다. */
export function toPsSeed(seed: string): string {
  return `sodium,${createHash('sha256').update(seed).digest('hex').slice(0, 32)}`;
}
