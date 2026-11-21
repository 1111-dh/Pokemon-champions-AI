"""복기 (PROJECT_PLAN 10절): 저장한 배틀 기록을 다시 재생하며 턴별 승률 변화를 구하고 실수 후보를 표시한다.

턴마다(그 턴 입력을 적용하기 전) 탐색으로 행동별 승률을 다시 계산한다.
- 그 턴의 승률 = 최선 수의 값 (상대 모델 대응, 이후는 기준선 봇 수준 운영 가정)
- 실제로 둔 수 = 그 턴 입력에서 내 쪽 첫 행동(기술, 메가 여부, 교체)
- 실수 후보: 최선 수 값 − 실제 수 값 ≥ mistake (기본 0.10)
- 정상 판단(운): 실제 수가 최선에 가까웠는데(차이 < near, 기본 0.05) 다음 턴 승률이 drop(기본 0.15) 이상 떨어짐
마지막 점은 결과(승 1, 패 0)다. 그래프는 SVG로 저장한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pokechamp.battle.events import Event
from pokechamp.battle.search import Searcher, SearchConfig
from pokechamp.battle.session import BattleSession
from pokechamp.battle.store import load_battle
from pokechamp.input.battle_text import BattleText
from pokechamp.paths import ROOT


def actual_action(events: list[Event], session: BattleSession) -> tuple | None:
    mega = False
    for e in events:
        if e.side != "me":
            continue
        if e.kind == "mega":
            mega = True
        elif e.kind == "move":
            return ("move", e.move, mega)
        elif e.kind == "switch":
            return ("switch", session.res(e.species))
    return None


def review(conn, battle_id, client=None, cfg: SearchConfig | None = None, mistake: float = 0.10, near: float = 0.05,
           drop: float = 0.15, log=lambda s: None) -> dict[str, Any]:
    b = load_battle(conn, battle_id)
    rec = b["log"]
    bt = BattleText(conn)
    sess = BattleSession(conn, b["my_team"], rec["opp_species"], client, seed=f"review-{b['id']}")
    srch = Searcher(sess, rec["brought"], cfg or SearchConfig(seed=f"review-{b['id']}", model_reps=6))
    sess.feed([Event("switch", "me", species=sess.res(rec["brought"][0])),
               Event("switch", "opp", species=sess.res(bt.lookup("species", rec["opp_lead"]) or rec["opp_lead"]))])
    points: list[dict[str, Any]] = []
    turn = 0
    for t in rec["turns"]:
        st = sess.state
        me = st.me.mons.get(st.me.active or "")
        switch_phase = me is None or me.fainted
        if not switch_phase:
            turn += 1
            sess.feed([Event("turn", amount=turn)])
        events = bt.parse(t["input"]).events
        act = actual_action(events, sess)
        point: dict[str, Any] = {"turn": turn, "input": t["input"], "phase": "switch" if switch_phase else "move"}
        try:
            if switch_phase:
                alive = [sess.res(x) for x in rec["brought"] if not st.me.mons[sess.res(x)].fainted]
                r = srch.choose_switch(alive) if alive else None
                if r:
                    vals = {("switch", k): v for k, v in r["values"].items()}
                    point.update(values=vals, win=max(vals.values()))
            else:
                r = srch.choose()
                vals = {a: float(v) for a, v in zip(r["actions"], r["vs_model"])}
                point.update(values=vals, win=max(vals.values()))
        except Exception as ex:  # noqa: BLE001
            point["error"] = str(ex)
        if "values" in point:
            best = max(point["values"], key=point["values"].get)
            point["best"], point["actual"] = best, act
            if act in point["values"]:
                point["regret"] = point["win"] - point["values"][act]
        points.append(point)
        log(f"  턴 {turn}{' (교체)' if switch_phase else ''}: 승률 {point.get('win', float('nan')):.0%}"
            + (f", 실제 수 손해 {point['regret']:.2f}" if 'regret' in point else ""))
        sess.feed(events)
    final = {"win": 1.0, "loss": 0.0, "tie": 0.5}.get(b.get("result") or "", None)
    for i, p in enumerate(points):
        nxt = points[i + 1].get("win") if i + 1 < len(points) else final
        p["next"] = nxt
        if p.get("regret") is not None and p["regret"] >= mistake:
            p["flag"] = "mistake"
        elif p.get("regret") is not None and p["regret"] < near and nxt is not None and p.get("win") is not None and p["win"] - nxt >= drop:
            p["flag"] = "unlucky"
    out = {"battle_id": b["id"], "result": b.get("result"), "source": b["source"], "points": points, "final": final,
           "mistakes": [p["turn"] for p in points if p.get("flag") == "mistake"],
           "unlucky": [p["turn"] for p in points if p.get("flag") == "unlucky"]}
    out["svg"] = svg_chart(out, bt)
    path = ROOT / "data" / "logs" / "battles" / f"review-{b['id']}.svg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out["svg"], encoding="utf-8")
    out["svg_path"] = str(path)
    return out


def label(action, bt: BattleText) -> str:
    if not action:
        return "-"
    if action[0] == "move":
        return bt.name("moves", action[1]) + (" + 메가" if len(action) > 2 and action[2] else "")
    return f"교체 {bt.name('species', action[1])}"


def svg_chart(r: dict[str, Any], bt: BattleText, w: int = 720, h: int = 260) -> str:
    """턴별 승률 선 그래프 (외부 라이브러리 없이 SVG). 빨강 = 실수 후보, 주황 = 정상 판단(운)."""
    pts = [p for p in r["points"] if p.get("win") is not None]
    xs = list(range(len(pts))) + ([len(pts)] if r["final"] is not None else [])
    ys = [p["win"] for p in pts] + ([r["final"]] if r["final"] is not None else [])
    if not ys:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'></svg>"
    pad_l, pad_r, pad_t, pad_b = 44, 16, 16, 36
    X = lambda i: pad_l + (w - pad_l - pad_r) * (i / max(1, len(ys) - 1))   # noqa: E731
    Y = lambda v: pad_t + (h - pad_t - pad_b) * (1 - v)                       # noqa: E731
    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {w} {h}' width='{w}' height='{h}' "
             "font-family='system-ui, sans-serif' font-size='11'>",
             f"<rect width='{w}' height='{h}' fill='white'/>"]
    for v in (0, 0.25, 0.5, 0.75, 1):
        parts.append(f"<line x1='{pad_l}' x2='{w - pad_r}' y1='{Y(v):.1f}' y2='{Y(v):.1f}' stroke='#e2e6ea' "
                     f"stroke-dasharray='{'0' if v in (0, 1) else '3,3'}'/>")
        parts.append(f"<text x='{pad_l - 6}' y='{Y(v) + 4:.1f}' text-anchor='end' fill='#56606b'>{int(v * 100)}%</text>")
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(i):.1f},{Y(y):.1f}" for i, y in enumerate(ys))
    parts.append(f"<path d='{path}' fill='none' stroke='#2347c5' stroke-width='2'/>")
    for i, p in enumerate(pts):
        color = {"mistake": "#d64545", "unlucky": "#f2b233"}.get(p.get("flag"), "#2347c5")
        rad = 5 if p.get("flag") else 3
        tip = f"턴 {p['turn']}: 승률 {p['win']:.0%} · 실제 {label(p.get('actual'), bt)} · 최선 {label(p.get('best'), bt)}"
        parts.append(f"<circle cx='{X(i):.1f}' cy='{Y(p['win']):.1f}' r='{rad}' fill='{color}'><title>{tip}</title></circle>")
        parts.append(f"<text x='{X(i):.1f}' y='{h - pad_b + 16}' text-anchor='middle' fill='#56606b'>{p['turn']}</text>")
    if r["final"] is not None:
        parts.append(f"<text x='{X(len(ys) - 1):.1f}' y='{h - pad_b + 16}' text-anchor='middle' fill='#56606b'>"
                     f"{'승' if r['final'] == 1 else '패' if r['final'] == 0 else '무'}</text>")
    parts.append(f"<text x='{pad_l}' y='{h - 6}' fill='#56606b'>턴 (빨강: 실수 후보 · 주황: 정상 판단이었지만 운으로 하락)</text>")
    parts.append("</svg>")
    return "".join(parts)


def text_report(r: dict[str, Any], conn) -> str:
    bt = BattleText(conn)
    L = [f"복기: my_battles #{r['battle_id']} ({'실제 배틀' if r['source'] == 'manual' else '자동 배틀'}) · 결과 {r['result'] or '미기록'}"]
    for p in r["points"]:
        if "win" not in p:
            L.append(f"  턴 {p['turn']}: 계산 실패 ({p.get('error', '')})")
            continue
        flag = {"mistake": "  ← 실수 후보", "unlucky": "  ← 정상 판단(운)"}.get(p.get("flag"), "")
        reg = f" (손해 {p['regret']:.2f})" if p.get("regret") is not None else ""
        L.append(f"  턴 {p['turn']:>2}{' 교체' if p['phase'] == 'switch' else '    '} 승률 {p['win']:>4.0%} · 실제 {label(p.get('actual'), bt)}{reg}"
                 f" · 최선 {label(p.get('best'), bt)}{flag}")
    L.append(f"실수 후보 턴: {', '.join(map(str, r['mistakes'])) or '없음'} · 운으로 떨어진 턴: {', '.join(map(str, r['unlucky'])) or '없음'}")
    L.append(f"그래프: {r['svg_path']}")
    L.append("  (승률은 상대 세트 샘플 × 롤아웃으로 다시 계산한 추정이다. 인게임 결과는 성능 지표가 아니라 복기용이다)")
    return "\n".join(L)
