import asyncio
import logging
from typing import Callable, Optional

from git_service import sync_repository, extract_diff
from gitlab_client import post_mr_comment
from review_service import run_aider_review
from workspace_manager import cleanup_workspace, get_workspace_path

logger = logging.getLogger(__name__)


async def handle_review_task(
    settings,
    project_id: str,
    mr_iid: str,
    source_branch: str,
    target_branch: str,
    question: Optional[str] = None,
) -> None:
    """4단계 파이프라인: sync → diff → review → comment

    각 블로킹 호출을 asyncio.to_thread로 감싸 이벤트 루프를 블로킹하지 않는다.
    여러 MR이 동시에 들어와도 각자 독립적으로 병렬 실행된다.
    """
    workspace_path = get_workspace_path(settings, mr_iid)
    logger.info(f"작업공간: {workspace_path}")

    if not source_branch or not project_id:
        logger.error(
            f"❌ [MR #{mr_iid}] source_branch 혹은 project_id가 없습니다 "
            "(GitLab Webhook 테스트 페이로드일 수 있습니다.)"
        )
        return

    # 1단계: 저장소 동기화 (git subprocess — 스레드로 분리)
    ok = await asyncio.to_thread(sync_repository, settings, workspace_path, mr_iid, source_branch)
    if not ok:
        return

    # 2단계: Diff 추출 (git subprocess — 스레드로 분리)
    diff_result = await asyncio.to_thread(extract_diff, settings, workspace_path, mr_iid, source_branch, target_branch)
    if diff_result is None:
        return

    # 3단계: AI 리뷰 (aider subprocess, 최대 10분 — 스레드로 분리)
    review = await asyncio.to_thread(run_aider_review, settings, mr_iid, workspace_path, diff_result, question)
    if review is None:
        return

    # 4단계: GitLab 코멘트 전송 (HTTP 요청 — 스레드로 분리)
    await asyncio.to_thread(post_mr_comment, settings, project_id, mr_iid, review)


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

        comment_text = payload.get("object_attributes", {}).get("note", "").lower()
        if "@aider" not in comment_text:
            return {"status": "ignored"}

        mr_iid = str(merge_request.get("iid"))
        source_branch = merge_request.get("source_branch")
        target_branch = merge_request.get("target_branch", "main")
        clean_question = comment_text.replace("@aider", "").strip()

        logger.info(f"🔔 [MR #{mr_iid}] 멘션 감지. 답변 생성을 시작합니다.")
        add_background_task(
            handle_review_task,
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

        if action in ["open", "update"]:
            logger.info(f"🚀 [MR #{mr_iid}] 코드 변경({action}) 감지. 자동 리뷰를 시작합니다.")
            add_background_task(
                handle_review_task,
                settings, project_id, mr_iid, source_branch, target_branch,
            )
            return {"status": "auto_review_queued"}

        if state in ["closed", "merged"] or action in ["close", "merge"]:
            logger.info(f"🗑️ [MR #{mr_iid}] MR 종료 감지. 정리 작업을 시작합니다.")
            add_background_task(asyncio.to_thread, cleanup_workspace, settings, mr_iid)
            return {"status": "cleanup_queued"}

        return {"status": "ignored"}

    return {"status": "ignored"}
