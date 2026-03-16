# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.

import logging
from dataclasses import dataclass

from aider_bot.ai.structuring import run_aider_and_structure
from aider_bot.scm.diff import ReviewUnit

logger = logging.getLogger(__name__)


@dataclass
class UnitReviewFinding:
    severity: str
    title: str
    description: str
    file: str
    lines: str
    confidence: str


def _build_unit_review_prompt(unit: ReviewUnit) -> str:
    related = "\n".join(f"- {path}" for path in unit.related_paths) if unit.related_paths else "- 없음"
    tags = ", ".join(unit.tags) if unit.tags else "general"
    return f"""[역할]
우리 팀 수석 SRE이자 백엔드 코드 리뷰어.

[리뷰 대상]
- 파일: {unit.path}
- 변경 유형: {unit.change_type}
- 위험도 점수: {unit.risk_score}
- 태그: {tags}
- 연관 파일:
{related}

[지시]
- 실제 버그, 회귀, 누락된 검증, 보안/동시성/트랜잭션 위험만 지적한다
- 사소한 스타일 의견은 생략한다
- 근거가 약하면 지적하지 않는다
- 모든 텍스트는 한국어로 작성한다
- 각 지적에는 파일 경로와 가능하면 라인 범위를 포함한다
- severity는 critical, warning, suggestion 의미에 맞춰 서술한다
- 자유형 bullet/문단 형식으로 답해도 된다

[Diff]
```diff
{unit.diff}
```
"""


def run_aider_unit_review(mr_iid: str, workspace_path: str, unit: ReviewUnit) -> list[UnitReviewFinding]:
    data, raw = run_aider_and_structure(
        mr_iid,
        workspace_path,
        _build_unit_review_prompt(unit),
        schema="unit_review",
        files=[unit.path, *unit.related_paths],
    )
    if not data:
        logger.warning("⚠️ [MR #%s] unit review 파싱 실패: %s", mr_iid, unit.path)
        if logger.isEnabledFor(logging.DEBUG) and raw:
            logger.debug("🧾 [MR #%s] unit review 자유형 응답:\n%s", mr_iid, raw)
        return []

    findings: list[UnitReviewFinding] = []
    for item in data.get("findings", []):
        if not isinstance(item, dict):
            continue
        description = str(item.get("description", "")).strip()
        if not description:
            continue
        findings.append(
            UnitReviewFinding(
                severity=str(item.get("severity", "suggestion")).strip() or "suggestion",
                title=str(item.get("title", "")).strip(),
                description=description,
                file=str(item.get("file", unit.path)).strip() or unit.path,
                lines=str(item.get("lines", "")).strip(),
                confidence=str(item.get("confidence", "")).strip() or "medium",
            )
        )
    return findings
