# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import logging
import requests

from config import settings

logger = logging.getLogger(__name__)


def change_mr_overview(project_id: str, mr_iid: str, title: str, description: str, token: str) -> None:
    """GitLab MR의 제목과 설명을 AI 생성 보고서로 교체한다."""
    if not project_id or project_id == "None":
        logger.error(f"❌ [MR #{mr_iid}] project_id가 누락되어 overview를 수정할 수 없습니다.")
        return

    url = f"{settings.gitlab_api_base}/projects/{project_id}/merge_requests/{mr_iid}"
    logger.info(f"📡 [MR #{mr_iid}] MR overview 수정 시도: {url}")

    headers = {"PRIVATE-TOKEN": token}
    payload = {"title": title, "description": description}

    try:
        response = requests.put(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"✅ [MR #{mr_iid}] MR overview 수정 성공")
    except requests.RequestException as e:
        logger.error(f"❌ [MR #{mr_iid}] overview 수정 실패 (URL: {url}): {str(e)}")


def post_mr_comment(project_id: str, mr_iid: str, message: str, token: str) -> None:
    """GitLab MR에 코멘트를 전송한다."""
    if not project_id or project_id == "None":
        logger.error(f"❌ [MR #{mr_iid}] project_id가 누락되어 코멘트를 전송할 수 없습니다.")
        return

    url = f"{settings.gitlab_api_base}/projects/{project_id}/merge_requests/{mr_iid}/notes"
    logger.info(f"📡 [MR #{mr_iid}] 코멘트 전송 시도: {url}")

    headers = {"PRIVATE-TOKEN": token}
    body = (
        "<details>\n"
        "<summary>🤖 <strong>Aider AI 응답</strong> — 클릭하여 펼치기</summary>\n\n"
        f"{message}\n\n"
        "---\n"
        "<sub>🤖 Aider AI Code Review Bot 자동 생성 · "
        "멘션: <code>aider 질문내용</code></sub>\n"
        "</details>"
    )
    payload = {"body": body}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"✅ [MR #{mr_iid}] GitLab 코멘트 전송 성공")
    except requests.RequestException as e:
        logger.error(f"❌ [MR #{mr_iid}] 코멘트 전송 실패 (URL: {url}): {str(e)}")
