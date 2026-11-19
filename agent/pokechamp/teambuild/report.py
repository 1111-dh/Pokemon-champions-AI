"""파티 추천 결과를 사람이 읽는 글로 (CLI와 웹 공용)."""

from __future__ import annotations

from typing import Any

from pokechamp.teams.store import export_text
from pokechamp.teambuild.validate import fmt


def text_report(r: dict[str, Any], v: dict[str, Any] | None = None) -> str:
    L = [f"통계: {r['stats']} · 레귤레이션 {r['regulation']} · 정적 데이터 v{r['static_version']} · 시드 {r['seed']}"]
    for w in r["warnings"]:
        L.append(f"⚠️ {w}")
    s = r["score"]
    L += ["", f"팀 점수 {s['score']:.3f} = 기대값 {s['mean']:.3f} − {s['alpha']}×(기대값 − 하위{s['cvar_fraction']:.0%} 평균 {s['cvar']:.3f})"
          f" − {s['beta']}×시나리오 편차 {s['sd']:.3f}",
          "  (값은 빠른 상성 근사로 구한 선출 게임 균형 값이다. 실제 승률이 아니라 팀끼리 비교하는 점수로 본다)",
          "시나리오별 기대값: " + ", ".join(f"{x['name']} {x['expected']:.3f}" for x in r["scenarios"]), "", "추천 팀"]
    for i, m in enumerate(r["members"], 1):
        tag = " [고정]" if m["locked"] else ""
        mega = f" (메가: {m['mega']})" if m["mega"] else ""
        L.append(f"{i}. {m['line_ko']}{mega}{tag}")
        L.append(f"   역할: {m['role']} · 균형 선출 확률 {m['pick_rate']:.0%}")
    if r["mega"]["holders"]:
        L += ["", "메가 후보 점검"]
        for h in r["mega"]["holders"]:
            L.append(f"- {h['mega']} (선출 확률 {h['pick_rate']:.0%}) · 약한 상대: {', '.join(h['weak_to']) or '없음'}")
            for p in h["partners"]:
                L.append(f"    파트너 {' + '.join(p['pair'])}: 약한 상대의 {p['covers']:.0%}를 받아 줌")
    if r["weaknesses"]:
        L += ["", "남는 약점 (팀에서 가장 나은 답도 불리한 상대, 출현 가중치 순)"]
        for w in r["weaknesses"]:
            L.append(f"- {w['species']} (가중치 {w['weight']:.1%}): 최선 {w['best_answer']} {w['best_score']:.2f}")
    if r["local_search_changes"]:
        L += ["", "국소 탐색으로 바꾼 것: " + "; ".join(r["local_search_changes"])]
    if r["replacements"]:
        L += ["", "교체 후보"]
        for x in r["replacements"]:
            L.append(f"- {x['out']} → {x['line_ko']} (점수 {x['score_delta']:+.3f})")
    for n in r["notes"]:
        L.append(f"참고: {n}")
    rr = r.get("rerank")
    if rr:
        L += ["", f"3:3 시뮬레이션 재평가 ({rr['policy']})"]
        for c in rr["candidates"]:
            mark = " ← 선택" if c["chosen"] else ""
            L.append(f"- 후보 {c['rank']}: 승률 {c['win_rate']:.1%} · 점수 {c['score']:.3f} · {', '.join(c['members'])}{mark}")
        if rr.get("local"):
            lo = rr["local"]
            L.append(f"- 국소 탐색 뒤 {lo['win_rate']:.1%} (전 {lo['before']:.1%}, 차이 {lo['gap']:+.1%} ± {lo['se']:.1%})"
                     + (" → 유지" if lo["kept"] else " → 유의하게 낮아져 되돌림"))
    if v:
        L += ["", f"검증 (정밀 시뮬레이션, {v['policy']}, 추천과 다른 시드로 합성한 메타 팀 상대 짝지은 대전)",
              f"- 추천 팀: {fmt(v['recommended'])}",
              f"- 사용률 상위 팀 ({', '.join(v['usage_top_team'])}): {fmt(v['usage_top'])}",
              f"- 무작위 팀: {fmt(v['random'])}"]
        for k, name in (("vs_usage_top", "사용률 상위 팀"), ("vs_random", "무작위 팀")):
            d = v[k]
            if d.get("diff") is not None:
                L.append(f"- {name} 대비 차이: {d['diff']:+.1%} (95% 구간 {d['lo']:+.1%}~{d['hi']:+.1%}, 짝 {d['pairs']:,}개)")
    L += ["", f"계산: {r['elapsed_s']}초, 팀 평가 {r['evaluations']}회, 새 상성 {r['new_matchups']:,}쌍", "",
          "Showdown 형식", export_text(r["team"])]
    return "\n".join(L)
