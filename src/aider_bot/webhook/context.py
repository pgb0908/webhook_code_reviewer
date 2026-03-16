# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass

from aider_bot.config import settings
from aider_bot.scm.sync import sync_repository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MergeRequestContext:
    project_id: str
    project_path: str
    mr_iid: str
    source_branch: str
    token: str
    repo_url: str
    workspace_path: str
    reply_discussion_id: str = ""


def get_workspace_path(mr_iid: str, project_id: str, project_path: str) -> str:
    project_name = project_path.rstrip("/").split("/")[-1]
    workspace_name = f"{project_id}_{project_name}_mr_{mr_iid}"
    return os.path.join(settings.workspace_base, workspace_name)


def ensure_workspace_base() -> None:
    os.makedirs(settings.workspace_base, exist_ok=True)
    logger.info("워크스페이스 기본 경로 확인: %s", settings.workspace_base)


def cleanup_workspace(mr_iid: str, project_id: str, project_path: str) -> None:
    workspace_path = get_workspace_path(mr_iid, project_id, project_path)
    if os.path.exists(workspace_path):
        try:
            shutil.rmtree(workspace_path)
            logger.info("🧹 [MR #%s] MR이 종료되어 캐시 폴더를 안전하게 삭제했습니다.", mr_iid)
        except Exception as exc:
            logger.error("❌ [MR #%s] 폴더 삭제 실패: %s", mr_iid, exc)
    else:
        logger.info("[MR #%s] 삭제할 워크스페이스가 없습니다: %s", mr_iid, workspace_path)


def build_merge_request_context(
    project_id: str,
    project_path: str,
    mr_iid: str,
    source_branch: str,
    *,
    reply_discussion_id: str = "",
) -> MergeRequestContext | None:
    if not source_branch or not project_id:
        logger.error(
            "❌ [MR #%s] source_branch 혹은 project_id가 없습니다 "
            "(GitLab Webhook 테스트 페이로드일 수 있습니다.)",
            mr_iid,
        )
        return None

    token = settings.get_token(project_id)
    if not token:
        logger.error(
            "❌ [MR #%s] No token for project_id=%s. Add PROJECT_TOKEN_%s= to .env",
            mr_iid,
            project_id,
            project_id,
        )
        return None

    return MergeRequestContext(
        project_id=project_id,
        project_path=project_path,
        mr_iid=mr_iid,
        source_branch=source_branch,
        token=token,
        repo_url=settings.build_repo_url(token, project_path),
        workspace_path=get_workspace_path(mr_iid, project_id, project_path),
        reply_discussion_id=reply_discussion_id,
    )


def workspace_exists(context: MergeRequestContext) -> bool:
    return os.path.exists(os.path.join(context.workspace_path, ".git"))


async def sync_workspace(context: MergeRequestContext) -> bool:
    return await asyncio.to_thread(
        sync_repository,
        context.workspace_path,
        context.mr_iid,
        context.source_branch,
        context.repo_url,
    )


async def ensure_comment_workspace(context: MergeRequestContext) -> bool:
    if workspace_exists(context):
        logger.info("✅ [MR #%s] 기존 workspace 재사용. sync 생략.", context.mr_iid)
        return True

    logger.info("📥 [MR #%s] workspace 없음. 저장소를 먼저 받아옵니다.", context.mr_iid)
    return await sync_workspace(context)
