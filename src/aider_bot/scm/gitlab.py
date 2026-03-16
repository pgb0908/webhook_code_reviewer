# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import logging
import requests

from aider_bot.ai.output import sanitize_gitlab_markdown
from aider_bot.config import settings

logger = logging.getLogger(__name__)


def _headers(token: str) -> dict[str, str]:
    return {"PRIVATE-TOKEN": token}


def _mr_url(project_id: str, mr_iid: str, suffix: str = "") -> str:
    base = f"{settings.gitlab_api_base}/projects/{project_id}/merge_requests/{mr_iid}"
    return f"{base}{suffix}"


def _request(method: str, url: str, *, headers: dict[str, str], **kwargs) -> requests.Response | None:
    try:
        response = requests.request(method, url, headers=headers, timeout=10, **kwargs)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        logger.error("❌ GitLab 요청 실패 (%s %s): %s", method, url, exc)
        return None


def change_mr_overview(project_id: str, mr_iid: str, title: str, description: str, token: str) -> None:
    """GitLab MR의 제목과 설명을 AI 생성 보고서로 교체한다."""
    if not project_id or project_id == "None":
        logger.error(f"❌ [MR #{mr_iid}] project_id가 누락되어 overview를 수정할 수 없습니다.")
        return

    url = _mr_url(project_id, mr_iid)
    logger.info(f"📡 [MR #{mr_iid}] MR overview 수정 시도: {url}")

    payload = {"title": title, "description": description}
    response = _request("PUT", url, headers=_headers(token), json=payload)
    if response is not None:
        logger.info(f"✅ [MR #{mr_iid}] MR overview 수정 성공")


def post_mr_comment(project_id: str, mr_iid: str, message: str, token: str) -> None:
    """GitLab MR에 코멘트를 전송한다."""
    if not project_id or project_id == "None":
        logger.error(f"❌ [MR #{mr_iid}] project_id가 누락되어 코멘트를 전송할 수 없습니다.")
        return

    url = _mr_url(project_id, mr_iid, "/notes")
    logger.info(f"📡 [MR #{mr_iid}] 코멘트 전송 시도: {url}")

    safe_message = sanitize_gitlab_markdown(message)
    body = (
        "<details>\n"
        "<summary>🤖 <strong>Aider AI 응답</strong> — 클릭하여 펼치기</summary>\n\n"
        f"{safe_message}\n\n"
        "---\n"
        "<sub>🤖 Aider AI Code Review Bot 자동 생성 · "
        "멘션: <code>aider 질문내용</code></sub>\n"
        "</details>"
    )
    payload = {"body": body}

    response = _request("POST", url, headers=_headers(token), json=payload)
    if response is not None:
        logger.info(f"✅ [MR #{mr_iid}] GitLab 코멘트 전송 성공")


def reply_to_mr_discussion(project_id: str, mr_iid: str, discussion_id: str, message: str, token: str) -> bool:
    """GitLab MR discussion thread에 답글을 전송한다."""
    if not project_id or project_id == "None":
        logger.error("❌ [MR #%s] project_id가 누락되어 discussion reply를 전송할 수 없습니다.", mr_iid)
        return False
    if not discussion_id:
        logger.error("❌ [MR #%s] discussion_id가 누락되어 discussion reply를 전송할 수 없습니다.", mr_iid)
        return False

    url = _mr_url(project_id, mr_iid, f"/discussions/{discussion_id}/notes")
    logger.info("📡 [MR #%s] discussion reply 전송 시도: %s", mr_iid, url)

    safe_message = sanitize_gitlab_markdown(message)
    body = (
        "<details open>\n"
        "<summary>🤖 <strong>Aider AI 응답</strong></summary>\n\n"
        f"{safe_message}\n\n"
        "</details>"
    )
    payload = {"body": body}

    response = _request("POST", url, headers=_headers(token), json=payload)
    if response is not None:
        logger.info("✅ [MR #%s] GitLab discussion reply 전송 성공", mr_iid)
        return True
    return False


def get_mr_diff_refs(project_id: str, mr_iid: str, token: str) -> dict | None:
    """최신 MR diff refs(base/start/head)를 조회한다."""
    url = _mr_url(project_id, mr_iid)
    response = _request("GET", url, headers=_headers(token))
    if response is None:
        logger.error("❌ [MR #%s] MR diff refs 조회 실패", mr_iid)
        return None

    try:
        data = response.json()
    except ValueError:
        logger.error("❌ [MR #%s] MR diff refs JSON 파싱 실패", mr_iid)
        return None

    diff_refs = data.get("diff_refs")
    if not isinstance(diff_refs, dict):
        logger.error("❌ [MR #%s] MR diff refs가 비어 있습니다", mr_iid)
        return None
    return diff_refs


def post_mr_diff_discussion(project_id: str, mr_iid: str, body: str, position: dict[str, str | int], token: str) -> bool:
    """GitLab MR diff 라인에 discussion을 생성한다."""
    if not project_id or project_id == "None":
        logger.error(f"❌ [MR #{mr_iid}] project_id가 누락되어 diff discussion을 전송할 수 없습니다.")
        return False

    url = _mr_url(project_id, mr_iid, "/discussions")
    payload = {"body": sanitize_gitlab_markdown(body)}
    for key, value in position.items():
        payload[f"position[{key}]"] = value

    response = _request("POST", url, headers=_headers(token), data=payload)
    if response is not None:
        logger.info("✅ [MR #%s] GitLab diff discussion 전송 성공 (%s:%s)", mr_iid, position.get("new_path"), position.get("new_line"))
        return True
    return False
