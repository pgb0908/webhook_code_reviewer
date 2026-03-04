import asyncio
import logging
import os
from typing import Optional

from config import settings
from git.sync import sync_repository
from git.diff import extract_diff
from gitlab.client import change_mr_overview, post_mr_comment
from ai.overview import run_aider_overview
from ai.comment import run_aider_comment
from workspace.manager import get_workspace_path

logger = logging.getLogger(__name__)


async def handle_comment_task(
    project_id: str,
    project_path: str,
    mr_iid: str,
    source_branch: str,
    target_branch: str,
    question: Optional[str] = None,
) -> None:
    """코멘트 질의응답 파이프라인: (sync →) aider → comment

    workspace가 이미 존재하면 sync를 건너뛴다.
    @aider 멘션은 코드 변경이 없으므로 open 때 받아온 repo를 그대로 사용한다.
    """
    if not source_branch or not project_id:
        logger.error(
            f"❌ [MR #{mr_iid}] source_branch 혹은 project_id가 없습니다 "
            "(GitLab Webhook 테스트 페이로드일 수 있습니다.)"
        )
        return

    token = settings.get_token(project_id)
    if not token:
        logger.error(
            f"❌ [MR #{mr_iid}] No token for project_id={project_id}. "
            f"Add PROJECT_TOKEN_{project_id}= to .env"
        )
        return
    repo_url = settings.build_repo_url(token, project_path)

    workspace_path = get_workspace_path(mr_iid, project_id, project_path)
    logger.info(f"작업공간: {workspace_path}")

    # workspace가 없을 때(서버 재시작 등)만 clone
    if not os.path.exists(os.path.join(workspace_path, ".git")):
        logger.info(f"📥 [MR #{mr_iid}] workspace 없음. 저장소를 먼저 받아옵니다.")
        ok = await asyncio.to_thread(sync_repository, workspace_path, mr_iid, source_branch, repo_url)
        if not ok:
            return
    else:
        logger.info(f"✅ [MR #{mr_iid}] 기존 workspace 재사용. sync 생략.")

    # AI 응답 생성 (aider subprocess, 최대 10분 — 스레드로 분리)
    response = await asyncio.to_thread(run_aider_comment, mr_iid, workspace_path, question)
    if response is None:
        return

    # GitLab 코멘트 전송 (HTTP 요청 — 스레드로 분리)
    await asyncio.to_thread(post_mr_comment, project_id, mr_iid, response, token)


async def handle_overview_task(
    project_id: str,
    project_path: str,
    mr_iid: str,
    source_branch: str,
    target_branch: str,
    original_title: str,
) -> None:
    """4단계 파이프라인: sync → diff → overview → MR 제목/설명 교체"""
    if not source_branch or not project_id:
        logger.error(
            f"❌ [MR #{mr_iid}] source_branch 혹은 project_id가 없습니다 "
            "(GitLab Webhook 테스트 페이로드일 수 있습니다.)"
        )
        return

    token = settings.get_token(project_id)
    if not token:
        logger.error(
            f"❌ [MR #{mr_iid}] No token for project_id={project_id}. "
            f"Add PROJECT_TOKEN_{project_id}= to .env"
        )
        return
    repo_url = settings.build_repo_url(token, project_path)

    workspace_path = get_workspace_path(mr_iid, project_id, project_path)
    logger.info(f"작업공간: {workspace_path}")

    # 1단계: 저장소 동기화
    ok = await asyncio.to_thread(sync_repository, workspace_path, mr_iid, source_branch, repo_url)
    if not ok:
        return

    # 2단계: Diff 추출
    diff_result = await asyncio.to_thread(extract_diff, workspace_path, mr_iid, source_branch, target_branch)
    if diff_result is None:
        return

    # 3단계: AI overview 생성
    result = await asyncio.to_thread(run_aider_overview, mr_iid, workspace_path, diff_result, original_title)
    if result is None:
        return
    title, description = result

    # 4단계: MR 제목과 설명 교체
    await asyncio.to_thread(change_mr_overview, project_id, mr_iid, title, description, token)
