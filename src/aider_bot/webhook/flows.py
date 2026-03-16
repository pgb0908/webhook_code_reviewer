# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.

import asyncio
import logging
from typing import Optional

from aider_bot.ai.comment import run_aider_comment
from aider_bot.ai.review.reviewer import UnitReviewFinding
from aider_bot.ai.review.pipeline import review_diff_and_build_overview, review_diff_and_collect_findings
from aider_bot.ai.review.validation import ValidationResult, run_validation
from aider_bot.scm.diff import build_diff_line_refs, extract_diff, extract_incremental_diff
from aider_bot.scm.gitlab import (
    change_mr_overview,
    get_mr_diff_refs,
    post_mr_comment,
    post_mr_diff_discussion,
    reply_to_mr_discussion,
)
from aider_bot.webhook.context import MergeRequestContext, ensure_comment_workspace, sync_workspace

logger = logging.getLogger(__name__)


async def _post_comment(context: MergeRequestContext, message: str) -> None:
    if context.reply_discussion_id:
        posted = await asyncio.to_thread(
            reply_to_mr_discussion,
            context.project_id,
            context.mr_iid,
            context.reply_discussion_id,
            message,
            context.token,
        )
        if posted:
            return
        logger.warning(
            "⚠️ [MR #%s] discussion reply 전송 실패. 일반 MR 코멘트로 폴백합니다.",
            context.mr_iid,
        )

    await asyncio.to_thread(post_mr_comment, context.project_id, context.mr_iid, message, context.token)


async def run_comment_pipeline(
    context: MergeRequestContext,
    target_branch: str,
    question: Optional[str] = None,
) -> None:
    if not await ensure_comment_workspace(context):
        return

    diff_result = await asyncio.to_thread(
        extract_diff,
        context.workspace_path,
        context.mr_iid,
        context.source_branch,
        target_branch,
    )
    diff_content = diff_result.content if diff_result else None

    response = await asyncio.to_thread(
        run_aider_comment,
        context.mr_iid,
        context.workspace_path,
        question,
        diff_content,
    )
    if response is None:
        return

    await _post_comment(context, response)


async def run_overview_pipeline(
    context: MergeRequestContext,
    target_branch: str,
    original_title: str,
) -> None:
    if not await sync_workspace(context):
        return

    diff_result = await asyncio.to_thread(
        extract_diff,
        context.workspace_path,
        context.mr_iid,
        context.source_branch,
        target_branch,
    )
    if diff_result is None:
        return

    result = await asyncio.to_thread(
        review_diff_and_build_overview,
        context.mr_iid,
        context.workspace_path,
        diff_result,
        original_title,
    )
    if result is None:
        return
    title, description = result

    await asyncio.to_thread(
        change_mr_overview,
        context.project_id,
        context.mr_iid,
        title,
        description,
        context.token,
    )


def _extract_first_line_number(lines: str) -> Optional[int]:
    digits = "".join(ch if ch.isdigit() else " " for ch in (lines or ""))
    for token in digits.split():
        try:
            value = int(token)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _nearest_changed_line(target: Optional[int], changed_lines: list[int]) -> Optional[int]:
    if not changed_lines:
        return None
    if target is None:
        return changed_lines[0]
    return min(changed_lines, key=lambda line: (abs(line - target), line))


def _render_inline_finding_markdown(finding: UnitReviewFinding) -> str:
    title = finding.title.strip() or "리뷰 포인트"
    parts = [f"**[{finding.severity}] {title}**"]
    if finding.description:
        parts.append(finding.description)
    if finding.confidence:
        parts.append(f"`confidence: {finding.confidence}`")
    return "\n\n".join(parts)


def _render_validation_failure_comment(result: ValidationResult) -> str:
    lines = [
        "## ❌ 빌드 검증 실패",
        f"`{result.command}` 실행 중 실패했습니다.",
    ]
    if result.output:
        lines.append(f"```text\n{result.output}\n```")
    return "\n\n".join(lines)


def _build_inline_discussion_payloads(
    findings_by_unit: dict[str, list[UnitReviewFinding]],
    diff_content: str,
    diff_refs: dict,
) -> tuple[list[tuple[UnitReviewFinding, dict[str, str | int]]], list[UnitReviewFinding]]:
    line_refs = build_diff_line_refs(diff_content)
    inline_items: list[tuple[UnitReviewFinding, dict[str, str | int]]] = []
    fallback_items: list[UnitReviewFinding] = []

    for findings in findings_by_unit.values():
        for finding in findings:
            line_ref = line_refs.get(finding.file)
            if not line_ref or not line_ref.new_lines:
                fallback_items.append(finding)
                continue

            new_line = _nearest_changed_line(_extract_first_line_number(finding.lines), line_ref.new_lines)
            if new_line is None:
                fallback_items.append(finding)
                continue

            inline_items.append(
                (
                    finding,
                    {
                        "position_type": "text",
                        "base_sha": diff_refs.get("base_sha", ""),
                        "head_sha": diff_refs.get("head_sha", ""),
                        "start_sha": diff_refs.get("start_sha", ""),
                        "old_path": line_ref.old_path,
                        "new_path": line_ref.new_path,
                        "new_line": new_line,
                    },
                )
            )
    return inline_items, fallback_items


def _flatten_findings(findings_by_unit: dict[str, list[UnitReviewFinding]]) -> list[UnitReviewFinding]:
    flattened: list[UnitReviewFinding] = []
    for findings in findings_by_unit.values():
        flattened.extend(findings)
    return flattened


async def _publish_inline_review(
    context: MergeRequestContext,
    diff_content: str,
    findings_by_unit: dict[str, list[UnitReviewFinding]],
) -> None:
    diff_refs = await asyncio.to_thread(get_mr_diff_refs, context.project_id, context.mr_iid, context.token)
    if not diff_refs:
        logger.warning("⚠️ [MR #%s] diff refs 조회 실패. 일반 코멘트로 폴백합니다.", context.mr_iid)
        fallback_lines = _flatten_findings(findings_by_unit)
        if fallback_lines:
            body = "## 📝 Push 코드리뷰\n" + "\n".join(
                f"- [{item.severity}] {item.description} (`{item.file}`)"
                for item in fallback_lines[:10]
            )
            await _post_comment(context, body)
        return

    inline_items, fallback_items = _build_inline_discussion_payloads(findings_by_unit, diff_content, diff_refs)
    total_findings = sum(len(items) for items in findings_by_unit.values())
    logger.info(
        "🧾 [MR #%s] push review 결과: findings=%d, inline_candidates=%d, fallback_candidates=%d",
        context.mr_iid,
        total_findings,
        len(inline_items),
        len(fallback_items),
    )

    success_count = 0
    for finding, position in inline_items[:10]:
        ok = await asyncio.to_thread(
            post_mr_diff_discussion,
            context.project_id,
            context.mr_iid,
            _render_inline_finding_markdown(finding),
            position,
            context.token,
        )
        if ok:
            success_count += 1
        else:
            fallback_items.append(finding)

    if fallback_items:
        lines = ["## 📝 Push 코드리뷰"]
        if success_count:
            lines.append(f"{success_count}개의 리뷰 포인트를 변경 라인에 직접 남겼습니다.")
        lines.append("아래 항목은 적절한 diff 위치를 찾지 못해 일반 코멘트로 남깁니다.")
        for item in fallback_items[:10]:
            line = f"- [{item.severity}] {item.description}"
            if item.file:
                line += f" (`{item.file}`)"
            if item.lines:
                line += f" [{item.lines}]"
            lines.append(line)
        await _post_comment(context, "\n".join(lines))
    elif success_count:
        await _post_comment(
            context,
            f"## 📝 Push 코드리뷰\n{success_count}개의 리뷰 포인트를 변경 라인에 직접 남겼습니다.",
        )


async def run_push_review_pipeline(
    context: MergeRequestContext,
    oldrev: str,
) -> None:
    if not await sync_workspace(context):
        return

    diff_result = await asyncio.to_thread(
        extract_incremental_diff,
        context.workspace_path,
        context.mr_iid,
        oldrev,
    )
    if diff_result is None or not diff_result.content.strip():
        logger.info("[MR #%s] 증분 diff 없음. 코드리뷰 생략.", context.mr_iid)
        return

    validation_result = await asyncio.to_thread(run_validation, context.workspace_path, context.mr_iid)
    validation_failed = bool(validation_result and not validation_result.ok)
    if validation_failed:
        logger.warning("⚠️ [MR #%s] 빌드 검증 실패. 결과를 코멘트로 남깁니다.", context.mr_iid)
        await _post_comment(context, _render_validation_failure_comment(validation_result))

    units, findings_by_unit = await asyncio.to_thread(
        review_diff_and_collect_findings,
        context.mr_iid,
        context.workspace_path,
        diff_result,
        force_first_unit_review=True,
    )
    logger.info("🧱 [MR #%s] review unit 수: %d", context.mr_iid, len(units))
    if not units:
        logger.info("[MR #%s] 리뷰 대상 unit 없음.", context.mr_iid)
        return

    total_findings = sum(len(items) for items in findings_by_unit.values())
    if total_findings == 0:
        if validation_failed:
            logger.info("ℹ️ [MR #%s] AI 리뷰 포인트는 없지만 빌드 검증 실패 코멘트가 이미 게시되었습니다.", context.mr_iid)
        else:
            logger.info("ℹ️ [MR #%s] 게시할 리뷰 포인트가 없어 push review를 종료합니다.", context.mr_iid)
            await _post_comment(context, "## 📝 Push 코드리뷰\n이번 증분 변경에서는 게시할 만한 주요 리뷰 포인트를 찾지 못했습니다.")
        return

    await _publish_inline_review(context, diff_result.content, findings_by_unit)
