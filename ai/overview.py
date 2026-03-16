# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import logging
from typing import Optional

from ai.reviewer import UnitReviewFinding
from ai.shared.output import render_overview_from_freeform, render_overview_markdown
from ai.shared.structured import run_aider_and_structure
from git.diff import ReviewUnit, detect_primary_language

logger = logging.getLogger(__name__)


def _summarize_change(unit: ReviewUnit) -> str:
    if unit.change_type == "added":
        return f"{unit.added_lines}줄 추가로 새 기능 또는 새 파일을 도입했습니다."
    if unit.change_type == "deleted":
        return f"{unit.deleted_lines}줄 삭제로 기존 동작을 제거하거나 단순화했습니다."
    if unit.change_type == "renamed":
        return "파일 이동 또는 이름 변경과 함께 로직 변경이 포함되었습니다."
    return f"{unit.added_lines}줄 추가, {unit.deleted_lines}줄 삭제로 동작을 수정했습니다."


def _build_overview_prompt(
    original_title: str,
    lang: str,
    units: list[ReviewUnit],
    findings_by_unit: dict[str, list[UnitReviewFinding]],
) -> str:
    persona = f"{lang + ' ' if lang else ''}백엔드 전문가"
    unit_lines = []
    for unit in units[:20]:
        findings = findings_by_unit.get(unit.unit_id, [])
        finding_lines = "\n".join(
            f"  - [{item.severity}] {item.description} ({item.file})"
            for item in findings[:3]
        ) or "  - 없음"
        unit_lines.append(
            "\n".join(
                [
                    f"- file: {unit.path}",
                    f"  change_type: {unit.change_type}",
                    f"  risk_score: {unit.risk_score}",
                    f"  summary: {_summarize_change(unit)}",
                    f"  findings:",
                    finding_lines,
                ]
            )
        )

    units_block = "\n".join(unit_lines)
    return f"""[작업 지시]
우리 팀 수석 SRE이자 {persona}로서, 아래 리뷰 단위 요약과 finding을 종합해
MR 설명용 개요를 한국어로 작성하라.

원래 MR 제목: {original_title}

[리뷰 단위 요약]
{units_block}

[작성 규칙]
- 첫 문단은 MR의 목적과 핵심 변경을 요약한다
- 실제 파일명을 인용해 주요 변경 파일을 설명한다
- 위험하거나 주의할 점이 있으면 분리해서 적는다
- 확실하지 않은 내용은 '(추측)'을 붙인다
- 태그, JSON, YAML 같은 구조화 포맷은 사용하지 않는다
"""


def synthesize_overview(
    mr_iid: str,
    workspace_path: str,
    units: list[ReviewUnit],
    findings_by_unit: dict[str, list[UnitReviewFinding]],
    original_title: str,
) -> Optional[tuple[str, str]]:
    lang = detect_primary_language([unit.path for unit in units])
    if lang:
        logger.info("🌐 [MR #%s] 감지된 주요 언어: %s", mr_iid, lang)

    data, raw = run_aider_and_structure(
        mr_iid,
        workspace_path,
        _build_overview_prompt(original_title, lang, units, findings_by_unit),
        schema="overview",
        files=[unit.path for unit in units[:10]],
    )
    if raw is None:
        return None

    if data and "title" in data:
        return render_overview_markdown(data)

    logger.warning("⚠️ [MR #%s] overview structured parse 실패, 자유형 응답 폴백 사용", mr_iid)
    return render_overview_from_freeform(raw, original_title)


def _build_push_review_markdown(title: str, units: list[ReviewUnit], findings_by_unit: dict[str, list[UnitReviewFinding]]) -> str:
    findings = []
    for unit in units:
        findings.extend(findings_by_unit.get(unit.unit_id, []))

    lines = ["## 📝 Push 코드리뷰"]
    if title:
        lines.append(f"**{title}**")

    changed_files = "\n".join(f"- `{unit.path}` (score={unit.risk_score})" for unit in units[:10])
    if changed_files:
        lines.append("\n### 변경 파일")
        lines.append(changed_files)

    if findings:
        lines.append("\n### 주요 리뷰 포인트")
        for finding in findings[:10]:
            line = f"- [{finding.severity}] {finding.description}"
            if finding.file:
                line += f" (`{finding.file}`)"
            lines.append(line)
    else:
        lines.append("\n리스크가 큰 결함은 발견되지 않았습니다.")

    return "\n".join(lines)


def build_push_review_comment(
    units: list[ReviewUnit],
    findings_by_unit: dict[str, list[UnitReviewFinding]],
    title: str = "",
) -> str:
    return _build_push_review_markdown(title, units, findings_by_unit)
