# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import asyncio
import logging

from fastapi import APIRouter, Request

from aider_bot.config import settings
from aider_bot.webhook.context import cleanup_workspace
from aider_bot.webhook.tasks import handle_overview_task, handle_comment_task, handle_push_review_task

logger = logging.getLogger(__name__)

router = APIRouter()
_active_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)


@router.post("/webhook")
async def gitlab_webhook(request: Request) -> dict:
    payload = await request.json()
    object_kind = payload.get("object_kind")
    project_id = str(
        payload.get("project_id")
        or payload.get("project", {}).get("id")
        or ""
    )
    project_path = payload.get("project", {}).get("path_with_namespace", "")

    logger.info(f"🔔 Webhook 수신 - Object Kind: {object_kind}, Project ID: {project_id}")

    # [이벤트 A] 댓글 멘션 시 -> 질의응답 실행
    if object_kind == "note":
        merge_request = payload.get("merge_request")
        if not merge_request:
            return {"status": "ignored"}

        # 봇 자신이 남긴 코멘트는 무시 (무한 루프 방지)
        author_username = payload.get("user", {}).get("username", "")
        if settings.bot_username and author_username == settings.bot_username:
            logger.info(f"🤖 봇 자신의 코멘트 무시 (author: {author_username})")
            return {"status": "ignored"}

        comment_text = payload.get("object_attributes", {}).get("note", "").lower()
        if "@aider" not in comment_text:
            return {"status": "ignored"}

        reply_discussion_id = str(
            payload.get("object_attributes", {}).get("discussion_id")
            or payload.get("object_attributes", {}).get("discussion", {}).get("id")
            or ""
        ).strip()
        mr_iid = str(merge_request.get("iid"))
        source_branch = merge_request.get("source_branch")
        target_branch = merge_request.get("target_branch", "main")
        clean_question = comment_text.replace("@aider", "").strip()

        logger.info(f"🔔 [MR #{mr_iid}] 멘션 감지. 답변 생성을 시작합니다.")
        _spawn(
            handle_comment_task(
                project_id,
                project_path,
                mr_iid,
                source_branch,
                target_branch,
                clean_question,
                reply_discussion_id=reply_discussion_id,
            )
        )
        return {"status": "queued"}

    # [이벤트 B] MR 상태 변경 시 -> 자동 리뷰 또는 폴더 정리
    if object_kind == "merge_request":
        mr_attributes = payload.get("object_attributes", {})
        mr_iid = str(mr_attributes.get("iid"))
        action = mr_attributes.get("action")
        state = mr_attributes.get("state")

        source_branch = mr_attributes.get("source_branch")
        target_branch = mr_attributes.get("target_branch", "main")

        if action == "open":
            mr_title = mr_attributes.get("title", "")
            logger.info(f"🆕 [MR #{mr_iid}] MR 생성 감지. overview 보고서 작성을 시작합니다.")
            _spawn(handle_overview_task(project_id, project_path, mr_iid, source_branch, target_branch, mr_title))
            return {"status": "overview_queued"}

        if action == "update":
            oldrev = mr_attributes.get("oldrev")
            if oldrev:
                logger.info(f"🔄 [MR #{mr_iid}] 새 커밋 push 감지. 증분 코드리뷰를 시작합니다.")
                _spawn(handle_push_review_task(project_id, project_path, mr_iid, source_branch, oldrev))
                return {"status": "push_review_queued"}
            return {"status": "ignored"}

        if state in ["closed", "merged"] or action in ["close", "merge"]:
            logger.info(f"🗑️ [MR #{mr_iid}] MR 종료 감지. 정리 작업을 시작합니다.")
            _spawn(asyncio.to_thread(cleanup_workspace, mr_iid, project_id, project_path))
            return {"status": "cleanup_queued"}

        return {"status": "ignored"}

    return {"status": "ignored"}
