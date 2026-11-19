"""자동 갱신 금지 원칙 (PROJECT_PLAN 0절, 12절 8번): 스케줄러, cron, 타이머 코드가 없어야 한다."""

import re

from pokechamp.paths import ROOT

FORBIDDEN = re.compile(r"\b(apscheduler|crontab|sched\.scheduler|threading\.Timer|setInterval|node-cron|schedule\.every)\b")


def test_no_scheduler_code():
    files = [*(ROOT / "agent" / "pokechamp").rglob("*.py"), *(ROOT / "engine" / "src").rglob("*.ts")]
    hits = [f"{p.relative_to(ROOT)}: {m.group(0)}" for p in files for m in FORBIDDEN.finditer(p.read_text(encoding="utf-8"))]
    assert not hits, f"자동 실행 코드 발견: {hits}"
