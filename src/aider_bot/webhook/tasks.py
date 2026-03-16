# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import asyncio
import logging
from typing import Optional

from aider_bot.webhook.flows import run_comment_pipeline, run_overview_pipeline, run_push_review_pipeline
from aider_bot.webhook.context import (
    build_merge_request_context,
)

logger = logging.getLogger(__name__)


async def _load_context(
    project_id: str,
    project_path: str,
    mr_iid: str,
    source_branch: str,
    *,
    reply_discussion_id: str = "",
):
    context = build_merge_request_context(
        project_id,
        project_path,
        mr_iid,
        source_branch,
        reply_discussion_id=reply_discussion_id,
    )
    if context is None:
        return None
    logger.info("작업공간: %s", context.workspace_path)
    return context


async def handle_comment_task(
    project_id: str,
    project_path: str,
    mr_iid: str,
    source_branch: str,
    target_branch: str,
    question: Optional[str] = None,
    reply_discussion_id: str = "",
) -> None:
    context = await _load_context(
        project_id,
        project_path,
        mr_iid,
        source_branch,
        reply_discussion_id=reply_discussion_id,
    )
    if context is None:
        return
    await run_comment_pipeline(context, target_branch, question)


async def handle_overview_task(
    project_id: str,
    project_path: str,
    mr_iid: str,
    source_branch: str,
    target_branch: str,
    original_title: str,
) -> None:
    context = await _load_context(project_id, project_path, mr_iid, source_branch)
    if context is None:
        return
    await run_overview_pipeline(context, target_branch, original_title)


async def handle_push_review_task(
    project_id: str,
    project_path: str,
    mr_iid: str,
    source_branch: str,
    oldrev: str,
) -> None:
    context = await _load_context(project_id, project_path, mr_iid, source_branch)
    if context is None:
        return
    await run_push_review_pipeline(context, oldrev)
