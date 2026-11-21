// 로컬 HTTP JSON 엔진 서버. 모든 엔드포인트는 단건 요청 또는 {"batch": [요청, ...]} 배치 요청을 받는다.
// 실행: npm run server  (환경변수 PORT, 기본 8787, 127.0.0.1에만 바인딩)
import http from 'node:http';
import * as E from './engine';

const ROUTES: Record<string, (req: any) => unknown> = {
  '/new_battle': E.newBattle,
  '/step': E.step,
  '/legal_actions': E.legalActions,
  '/simulate': E.simulate,
  '/validate_team': E.validate,
  '/random_team': E.randomTeam,
  '/damage': E.damage,
  '/matchup_fast': E.matchupFast,
  '/build_state': E.buildState,
  '/stats': E.stats,
  '/bot_choice': E.botChoice,
  '/moves_info': E.movesInfo,
};

function runOne(fn: (req: any) => unknown, body: any) {
  try {
    return { ok: true, result: fn(body) };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}

export function handle(pathname: string, body: any) {
  const fn = ROUTES[pathname];
  if (!fn) return { status: 404, payload: { ok: false, error: `없는 경로: ${pathname}` } };
  if (body && Array.isArray(body.batch)) {
    return { status: 200, payload: { ok: true, results: body.batch.map((b: any) => runOne(fn, b)) } };
  }
  const r = runOne(fn, body);
  return { status: r.ok ? 200 : 400, payload: r };
}

export function createServer() {
  return http.createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://localhost');
    if (req.method === 'GET' && url.pathname === '/health') {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ ok: true, routes: Object.keys(ROUTES) }));
      return;
    }
    if (req.method !== 'POST') {
      res.writeHead(405).end();
      return;
    }
    const chunks: Buffer[] = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => {
      let body: any;
      try {
        body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
      } catch {
        res.writeHead(400, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'JSON 해석 실패' }));
        return;
      }
      const { status, payload } = handle(url.pathname, body);
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(JSON.stringify(payload));
    });
  });
}

if (require.main === module) {
  const port = Number(process.env.PORT ?? 8787);
  createServer().listen(port, '127.0.0.1', () => console.log(`engine server: http://127.0.0.1:${port}`));
}
