import asyncio
import logging
import os
from typing import Callable, Optional

from git_service import sync_repository, extract_diff
from gitlab_client import change_mr_overview, post_mr_comment
from service.review_service import run_aider_overview
from service.comment_service import run_aider_comment
from workspace_manager import cleanup_workspace, get_workspace_path

logger = logging.getLogger(__name__)


async def handle_comment_task(
    settings,
    project_id: str,
    mr_iid: str,
    source_branch: str,
    target_branch: str,
    question: Optional[str] = None,
) -> None:
    """코멘트 질의응답 파이프라인: (sync →) aider → comment

    workspace가 이미 존재하면 sync를 건너뛴다.
    @aider 멘션은 코드 변경이 없으므로 open 때 받아온 repo를 그대로 사용한다.
    """
    workspace_path = get_workspace_path(settings, mr_iid)
    logger.info(f"작업공간: {workspace_path}")

    if not source_branch or not project_id:
        logger.error(
            f"❌ [MR #{mr_iid}] source_branch 혹은 project_id가 없습니다 "
            "(GitLab Webhook 테스트 페이로드일 수 있습니다.)"
        )
        return

    # workspace가 없을 때(서버 재시작 등)만 clone
    if not os.path.exists(os.path.join(workspace_path, ".git")):
        logger.info(f"📥 [MR #{mr_iid}] workspace 없음. 저장소를 먼저 받아옵니다.")
        ok = await asyncio.to_thread(sync_repository, settings, workspace_path, mr_iid, source_branch)
        if not ok:
            return
    else:
        logger.info(f"✅ [MR #{mr_iid}] 기존 workspace 재사용. sync 생략.")

    # AI 응답 생성 (aider subprocess, 최대 10분 — 스레드로 분리)
    response = await asyncio.to_thread(run_aider_comment, settings, mr_iid, workspace_path, question)
    if response is None:
        return

    # GitLab 코멘트 전송 (HTTP 요청 — 스레드로 분리)
    await asyncio.to_thread(post_mr_comment, settings, project_id, mr_iid, response)


async def handle_overview_task(
    settings,
    project_id: str,
    mr_iid: str,
    source_branch: str,
    target_branch: str,
    original_title: str,
) -> None:
    """4단계 파이프라인: sync → diff → overview → MR 제목/설명 교체"""
    workspace_path = get_workspace_path(settings, mr_iid)
    logger.info(f"작업공간: {workspace_path}")

    if not source_branch or not project_id:
        logger.error(
            f"❌ [MR #{mr_iid}] source_branch 혹은 project_id가 없습니다 "
            "(GitLab Webhook 테스트 페이로드일 수 있습니다.)"
        )
        return

    # 1단계: 저장소 동기화
    ok = await asyncio.to_thread(sync_repository, settings, workspace_path, mr_iid, source_branch)
    if not ok:
        return

    # 2단계: Diff 추출
    diff_result = await asyncio.to_thread(extract_diff, settings, workspace_path, mr_iid, source_branch, target_branch)
    if diff_result is None:
        return

    # 3단계: AI overview 생성
    result = await asyncio.to_thread(run_aider_overview, settings, mr_iid, workspace_path, diff_result, original_title)
    if result is None:
        return
    title, description = result

    # 4단계: MR 제목과 설명 교체
    await asyncio.to_thread(change_mr_overview, settings, project_id, mr_iid, title, description)


def route_webhook(payload: dict, settings, add_background_task: Callable) -> dict:
    """Webhook 이벤트를 라우팅한다. FastAPI를 직접 import하지 않아 테스트가 용이하다."""
    object_kind = payload.get("object_kind")
    project_id = str(
        payload.get("project_id")
        or payload.get("project", {}).get("id")
        or ""
    )

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

        mr_iid = str(merge_request.get("iid"))
        source_branch = merge_request.get("source_branch")
        target_branch = merge_request.get("target_branch", "main")
        clean_question = comment_text.replace("@aider", "").strip()

        logger.info(f"🔔 [MR #{mr_iid}] 멘션 감지. 답변 생성을 시작합니다.")
        add_background_task(
            handle_comment_task,
            settings, project_id, mr_iid, source_branch, target_branch, clean_question,
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
            add_background_task(
                handle_overview_task,
                settings, project_id, mr_iid, source_branch, target_branch, mr_title,
            )
            return {"status": "overview_queued"}

        # if action == "update":
        #     logger.info(f"🔄 [MR #{mr_iid}] 코드 변경(update) 감지. 자동 리뷰를 시작합니다.")
        #     add_background_task(
        #         handle_review_task,
        #         settings, project_id, mr_iid, source_branch, target_branch,
        #     )
        #     return {"status": "auto_review_queued"}

        if state in ["closed", "merged"] or action in ["close", "merge"]:
            logger.info(f"🗑️ [MR #{mr_iid}] MR 종료 감지. 정리 작업을 시작합니다.")
            add_background_task(asyncio.to_thread, cleanup_workspace, settings, mr_iid)
            return {"status": "cleanup_queued"}

        return {"status": "ignored"}

    return {"status": "ignored"}
