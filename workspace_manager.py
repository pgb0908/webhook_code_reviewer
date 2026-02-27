import os
import shutil
import logging

logger = logging.getLogger(__name__)


def get_workspace_path(settings, mr_iid: str) -> str:
    """project_path의 마지막 세그먼트를 이름으로 사용하여 워크스페이스 경로를 반환한다.

    예) anyapi/anyapi21/for_ai_review_test -> for_ai_review_test_mr_42
    """
    project_name = settings.project_path.rstrip("/").split("/")[-1]
    workspace_name = f"{project_name}_mr_{mr_iid}"
    return os.path.join(settings.workspace_base, workspace_name)


def ensure_workspace_base(settings) -> None:
    """앱 시작 시 기본 워크스페이스 디렉토리를 생성한다."""
    os.makedirs(settings.workspace_base, exist_ok=True)
    logger.info(f"워크스페이스 기본 경로 확인: {settings.workspace_base}")


def cleanup_workspace(settings, mr_iid: str) -> None:
    """MR 종료 시 워크스페이스 폴더를 삭제한다."""
    workspace_path = get_workspace_path(settings, mr_iid)
    if os.path.exists(workspace_path):
        try:
            shutil.rmtree(workspace_path)
            logger.info(f"🧹 [MR #{mr_iid}] MR이 종료되어 캐시 폴더를 안전하게 삭제했습니다.")
        except Exception as e:
            logger.error(f"❌ [MR #{mr_iid}] 폴더 삭제 실패: {str(e)}")
    else:
        logger.info(f"[MR #{mr_iid}] 삭제할 워크스페이스가 없습니다: {workspace_path}")
