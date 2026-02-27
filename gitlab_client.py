import logging
import requests

logger = logging.getLogger(__name__)


def change_mr_overview() -> None:
    """GitLab의 overview를 bot이 수정함"""


def post_mr_comment(settings, project_id: str, mr_iid: str, message: str) -> None:
    """GitLab MR에 코멘트를 전송한다."""
    if not project_id or project_id == "None":
        logger.error(f"❌ [MR #{mr_iid}] project_id가 누락되어 코멘트를 전송할 수 없습니다.")
        return

    url = f"{settings.gitlab_api_base}/projects/{project_id}/merge_requests/{mr_iid}/notes"
    logger.info(f"📡 [MR #{mr_iid}] 코멘트 전송 시도: {url}")

    headers = {"PRIVATE-TOKEN": settings.gitlab_token}
    payload = {"body": f"🤖 **Aider AI 리뷰**\n\n{message}"}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"✅ [MR #{mr_iid}] GitLab 코멘트 전송 성공")
    except requests.RequestException as e:
        logger.error(f"❌ [MR #{mr_iid}] 코멘트 전송 실패 (URL: {url}): {str(e)}")
