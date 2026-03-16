# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Optional

from aider_bot.ai.review.overview import build_push_review_comment, synthesize_overview
from aider_bot.ai.review.reviewer import UnitReviewFinding, run_aider_unit_review
from aider_bot.config import settings
from aider_bot.scm.diff import DiffResult, ReviewUnit, build_review_units
from aider_bot.ai.review.store import get_cached_unit, load_review_cache, save_review_cache, upsert_cached_unit

logger = logging.getLogger(__name__)

_MIN_REVIEW_SCORE = 20


def _serialize_finding(finding: UnitReviewFinding) -> dict:
    return asdict(finding)


def _deserialize_finding(data: dict) -> UnitReviewFinding:
    return UnitReviewFinding(
        severity=str(data.get("severity", "suggestion")),
        title=str(data.get("title", "")).strip(),
        description=str(data.get("description", "")).strip(),
        file=str(data.get("file", "")).strip(),
        lines=str(data.get("lines", "")).strip(),
        confidence=str(data.get("confidence", "")).strip(),
    )


def _should_deep_review(unit: ReviewUnit, reviewed_count: int) -> bool:
    if reviewed_count >= settings.max_deep_review_units:
        return False
    return unit.risk_score >= _MIN_REVIEW_SCORE


def _execute_deep_reviews(
    mr_iid: str,
    workspace_path: str,
    units: list[ReviewUnit],
) -> dict[str, list[UnitReviewFinding]]:
    if not units:
        return {}

    max_workers = max(1, settings.max_parallel_reviews)
    logger.info(
        "🚦 [MR #%s] deep review 병렬 실행: %d개 대상, 최대 동시성 %d",
        mr_iid,
        len(units),
        max_workers,
    )

    findings_by_unit: dict[str, list[UnitReviewFinding]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for unit in units:
            logger.info(
                "🔍 [MR #%s] deep review 예약: %s (score=%d)",
                mr_iid,
                unit.path,
                unit.risk_score,
            )
            future = executor.submit(run_aider_unit_review, mr_iid, workspace_path, unit)
            future_map[future] = unit

        for future in as_completed(future_map):
            unit = future_map[future]
            try:
                findings_by_unit[unit.unit_id] = future.result()
            except Exception as exc:
                logger.error("❌ [MR #%s] deep review 실행 실패: %s (%s)", mr_iid, unit.path, exc)
                findings_by_unit[unit.unit_id] = []

    return findings_by_unit


def _run_review_units(
    mr_iid: str,
    workspace_path: str,
    diff_result: DiffResult,
    *,
    force_first_unit_review: bool = False,
) -> tuple[list[ReviewUnit], dict[str, list[UnitReviewFinding]]]:
    units = build_review_units(diff_result.content)
    logger.info("🧱 [MR #%s] review unit 수: %d", mr_iid, len(units))

    cache = load_review_cache(workspace_path, diff_result.source_sha)
    reviewed_count = 0
    findings_by_unit: dict[str, list[UnitReviewFinding]] = {}
    deep_review_units: list[ReviewUnit] = []

    for unit in units:
        cached = get_cached_unit(cache, unit.unit_id)
        if cached:
            findings_by_unit[unit.unit_id] = [
                _deserialize_finding(item)
                for item in cached.get("findings", [])
                if isinstance(item, dict)
            ]
            continue

        if force_first_unit_review and reviewed_count == 0:
            deep_review_units.append(unit)
            reviewed_count += 1
            logger.info(
                "ℹ️ [MR #%s] push review 최소 보장 정책으로 deep review 강제: %s (score=%d)",
                mr_iid,
                unit.path,
                unit.risk_score,
            )
            continue

        if _should_deep_review(unit, reviewed_count):
            deep_review_units.append(unit)
            reviewed_count += 1
        else:
            findings_by_unit[unit.unit_id] = []

    reviewed_results = _execute_deep_reviews(mr_iid, workspace_path, deep_review_units)

    for unit in units:
        if unit.unit_id in reviewed_results:
            findings_by_unit[unit.unit_id] = reviewed_results[unit.unit_id]

        if unit.unit_id not in findings_by_unit:
            findings_by_unit[unit.unit_id] = []

        upsert_cached_unit(
            cache,
            unit.unit_id,
            {
                "path": unit.path,
                "risk_score": unit.risk_score,
                "tags": unit.tags,
                "findings": [_serialize_finding(item) for item in findings_by_unit[unit.unit_id]],
            },
        )

    save_review_cache(workspace_path, diff_result.source_sha, cache)
    return units, findings_by_unit


def review_diff_and_build_overview(
    mr_iid: str,
    workspace_path: str,
    diff_result: DiffResult,
    original_title: str,
) -> Optional[tuple[str, str]]:
    units, findings_by_unit = _run_review_units(mr_iid, workspace_path, diff_result)
    return synthesize_overview(mr_iid, workspace_path, units, findings_by_unit, original_title)


def review_diff_and_collect_findings(
    mr_iid: str,
    workspace_path: str,
    diff_result: DiffResult,
    *,
    force_first_unit_review: bool = False,
) -> tuple[list[ReviewUnit], dict[str, list[UnitReviewFinding]]]:
    return _run_review_units(
        mr_iid,
        workspace_path,
        diff_result,
        force_first_unit_review=force_first_unit_review,
    )


def review_diff_and_build_push_comment(
    mr_iid: str,
    workspace_path: str,
    diff_result: DiffResult,
    title: str = "",
) -> str:
    units, findings_by_unit = review_diff_and_collect_findings(
        mr_iid,
        workspace_path,
        diff_result,
        force_first_unit_review=True,
    )
    return build_push_review_comment(units, findings_by_unit, title=title)
