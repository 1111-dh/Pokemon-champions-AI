"""선출 추천 결과를 사람이 읽는 글로 (CLI와 웹 공용)."""

from __future__ import annotations

from typing import Any


def text_report(r: dict[str, Any], show_matrix: bool = False) -> str:
    m, o = r["matrix"], r["oracle"]
    L = [f"통계: {r['freshness']} · 레귤레이션 {r['regulation']} · 시드 {r['seed']}"]
    for w in r["warnings"]:
        L.append(f"⚠️ {w}")
    L += ["", "상대 6마리 (출전 확률은 상대 균형 전략 기준, 메가스톤 확률은 세트 통계 기준)"]
    for x in r["opponent"]:
        mega = f" · 메가스톤 {x['mega_stone_probability']:.0%}" if x["mega_stone_probability"] else ""
        L.append(f"- {x['species']}: 출전 {x['bring_probability']:.0%}{mega}")
    L += ["", "추천 선출 (이 확률대로 무작위로 고르세요)"]
    for i, p in enumerate(r["picks"], 1):
        mega = f" · 메가: {p['mega']}" if p["mega"] else ""
        br = " · 상대 모델 최선 응수" if p["opponent_model_best_response"] else ""
        L.append(f"{i}. {p['probability']:.0%}  선봉 {p['lead']} + {', '.join(p['members'][1:])}  "
                 f"(슬롯 {'·'.join(map(str, p['slots']))}){mega}")
        reason = []
        if p["strong"]:
            reason.append(f"강함: {'·'.join(p['strong'])}")
        if p["weak"]:
            reason.append(f"불리: {'·'.join(p['weak'])}")
        L.append(f"   예상 승률 {p['win_vs_equilibrium']:.0%} (상대 균형) / {p['win_vs_model']:.0%} (상대 모델)"
                 f" · 정밀 칸 {p['precise_share']:.0%}{br}" + (f" · {' / '.join(reason)}" if reason else ""))
    L += ["", f"전체 기대 승률: {r['expected_win_vs_equilibrium']:.1%} (상대가 균형으로 고를 때), "
              f"{r['expected_win_vs_model']:.1%} (상대 모델대로 고를 때)",
          f"  최종 확률 = {1 - r['opponent_model_mix']:.0%} × 균형 해 + {r['opponent_model_mix']:.0%} × 상대 모델 최선 응수"]
    if r["opponent_likely_picks"]:
        L += ["", "상대 예상 선출 (균형 기준)"]
        for x in r["opponent_likely_picks"]:
            L.append(f"- {x['probability']:.0%}  선봉 {x['lead']} + {', '.join(x['members'][1:])}")
    budget = f"예산 {r['max_games']:,}판" if r.get("max_games") is not None else f"예산 {r['budget_s']:.0f}초"
    L += ["", f"계산: {r['elapsed_s']}초 ({budget}), 3:3 시뮬레이션 {r['games']:,}판"
              + (f" (오류 {r['errors']})" if r["errors"] else "")
              + f", 더블 오라클 {o['iterations']}회 {'수렴' if o['converged'] else '예산에서 멈춤 (수렴 전)'}",
          f"  보수 행렬 {m['size'][0]}×{m['size'][1]} 중 정밀 계산 {m['precise_cells']}칸, 근사값만 쓴 칸 {m['approx_only_share']:.0%}."
          f" 후보 {m['candidates'][0]}×{m['candidates'][1]} 안에서 정밀 칸 {m['candidate_precise_share']:.0%},"
          f" 균형 확률 가중 정밀 비율 {m['weighted_precise_share']:.0%}",
          f"  {r['policy']}"]
    if show_matrix:
        L += ["", "후보 보수 행렬 (내 승률, *는 근사값을 보정한 칸)", "            " + " ".join(c[5:].rjust(5) for c in m["cols"])]
        for name, vals, pre in zip(m["rows"], m["values"], m["precise"]):
            L.append(f"  {name}  " + " ".join(f"{v:5.2f}" if ok else f"{v:4.2f}*" for v, ok in zip(vals, pre)))
    return "\n".join(L)
