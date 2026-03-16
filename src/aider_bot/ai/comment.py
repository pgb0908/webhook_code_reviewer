# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import logging
import os
import re
from typing import Optional

from aider_bot.config import settings
from aider_bot.ai.output import render_comment_from_freeform, render_comment_markdown
from aider_bot.ai.structuring import run_aider_and_structure
from aider_bot.scm.diff import rank_changed_files

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s`'\"<>()]+")


def _mask_urls(text: str) -> str:
    return _URL_RE.sub("<URL>", text)


def _select_comment_context_files(diff_content: Optional[str]) -> list[str]:
    if not diff_content:
        return []

    excluded_exts = {
        ext.strip().lower()
        for ext in settings.comment_exclude_extensions.split(",")
        if ext.strip()
    }

    selected: list[str] = []
    for path in rank_changed_files(diff_content, max_files=max(1, settings.comment_max_context_files * 3)):
        ext = os.path.splitext(path)[1].lower()
        if ext in excluded_exts:
            continue
        selected.append(path)
        if len(selected) >= settings.comment_max_context_files:
            break
    return selected


def _build_user_ask_prompt(question: Optional[str], diff_content: Optional[str] = None) -> str:
    user_question = question if question else "이 Merge Request의 변경 사항에 대해 전반적인 코드 리뷰를 해줘."

    diff_section = ""
    if diff_content and diff_content.strip():
        masked_diff = _mask_urls(diff_content)
        diff_section = f"""[Diff]
```diff
{masked_diff}
```

"""

    return f"""[역할]
우리 팀 수석 SRE이자 백엔드 전문가.

⚠️ 중요: 최종 답변은 반드시 한국어로만 작성하라. 영어 사용 절대 금지.

{diff_section}[질문]
{_mask_urls(user_question)}

[답변 규칙]
- 결론부터 시작한다 (질문 반복 금지)
- Repo Map과 제공된 Diff를 근거로 답변한다
- Repo Map의 실제 파일명·함수명을 근거로 인용한다
- 수정 제안은 before/after로 표현한다
- 문제 위치가 분명하면 파일 경로를 반드시 적는다
- 수정 제안을 할 때는 복붙 가능한 최소 수정 코드만 제시한다
- 리뷰 포인트 심각도: critical | warning | suggestion
- 확실하지 않으면 "(추측)" 명시
- 코드를 직접 수정하지 말고 제안만 한다
- 응답 형식은 자유롭게 작성하되, 결론과 근거가 분명해야 한다
- 수정 제안이 있으면 파일명, 문제점, 개선 예시 코드를 함께 적는다
"""


def run_aider_comment(
        mr_iid: str,
        workspace_path: str,
        question: Optional[str],
        diff_content: Optional[str] = None,
) -> Optional[str]:
    """Aider CLI를 실행하여 응답 텍스트를 반환한다. 실패 시 None."""
    logger.info(f"🧠 [MR #{mr_iid}] 질문에 대한 응답 생성 중...")
    prompt = _build_user_ask_prompt(question, diff_content)
    files = _select_comment_context_files(diff_content)
    data, raw = run_aider_and_structure(mr_iid, workspace_path, prompt, schema="comment", files=files)
    if raw is None:
        return None

    if data and "conclusion" in data:
        return render_comment_markdown(data)

    logger.warning(f"⚠️ [MR #{mr_iid}] comment structured parse 실패, 자유형 응답 폴백 사용")
    return render_comment_from_freeform(raw)
