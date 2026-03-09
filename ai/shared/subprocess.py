# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import os
import re
import subprocess
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# ANSI 이스케이프 코드 제거용 정규식
_ANSI_ESCAPE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")

# LLM 연결 실패 키워드 (aider가 exit 0으로 끝내더라도 감지)
_CONNECTION_ERROR_PATTERNS = re.compile(
    r"(Connection error|InternalServerError|litellm\.|API provider.*down|Retrying in \d)",
    re.IGNORECASE,
)


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def _strip_box_drawing(text: str) -> str:
    """aider UI 구분선(─, │ 등 박스 문자) 제거"""
    return re.sub(r"[─│╭╮╰╯┤├┬┴┼═║╔╗╚╝╠╣╦╩╪╫]+\n?", "", text)


# aider 시작/종료 메타 메시지 패턴
_AIDER_META_LINE = re.compile(
    r"^(Aider v|Model:|Git repo:|Repo-map:|Added |Tokens:|Applied edit to|"
    r"Only \d+ reflections|Summarization failed|can't summarize|"
    r"Auto-committing|Warning:|No changes made|"
    # "server.cpp I've reviewed…" 형태: 파일명 뒤에 공백+대문자 문장
    r"[A-Za-z0-9_./-]+\.[a-zA-Z]{1,5}\s+[A-Z]|"
    # "server.h" 같은 단독 파일명 행
    r"[A-Za-z0-9_./-]+\.[a-zA-Z]{1,5}\s*$"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# 프로그레스 바 패턴: [█░ ...] N%
_PROGRESS_BAR = re.compile(r"\[[\s█░▓▒]+\]\s*\d+%")

# 마크다운 정규화용 정규식
_HEADING_RE = re.compile(r"(?<!\n)\n(#{1,3} )")
_HEADING_TRAIL_RE = re.compile(r"^(#{1,3} [^\n]+)\n(?!\n)", re.MULTILINE)


def _normalize_markdown(text: str) -> str:
    """마크다운을 GitLab에서 깔끔하게 렌더링되도록 정규화한다."""
    lines = [line.rstrip() for line in text.splitlines()]
    output = "\n".join(lines)
    # • 불릿 → 마크다운 - 불릿 (들여쓰기 보존)
    output = re.sub(r"^(\s*)•\s*", r"\1- ", output, flags=re.MULTILINE)
    # heading 앞 빈줄 보장
    output = _HEADING_RE.sub(r"\n\n\1", output)
    # heading 뒤 빈줄 보장
    output = _HEADING_TRAIL_RE.sub(r"\1\n\n", output)
    # 3개 이상 연속 빈줄 → 2개
    output = re.sub(r"\n{3,}", "\n\n", output)
    return output.strip()


def _extract_llm_response(text: str) -> str:
    """aider stdout에서 LLM의 실제 텍스트 응답만 추출한다.
    시작/종료 메타 줄, 프로그레스 바, 빈 줄 연속을 제거한다.
    """
    lines = text.splitlines()
    result = []
    for line in lines:
        stripped = line.rstrip()
        if _AIDER_META_LINE.match(stripped):
            continue
        if _PROGRESS_BAR.search(stripped):
            continue
        result.append(stripped)

    # 앞뒤 연속 빈 줄 정리
    output = "\n".join(result).strip()
    output = re.sub(r"\n{3,}", "\n\n", output)
    return output


def run_aider_subprocess(
    mr_iid: str,
    workspace_path: str,
    prompt: str,
    file_paths: Optional[list[str]] = None,
) -> Optional[str]:
    """Aider CLI subprocess를 실행하고 정제된 stdout을 반환한다. 실패 시 None."""
    env = os.environ.copy()
    env["OPENAI_API_BASE"] = settings.remote_llm_base_url
    env["OPENAI_API_KEY"] = settings.remote_llm_api_key

    aider_command = [
        "aider",
        "--model", settings.remote_llm_model,
        "--api-key", "openai=none",
        "--no-auto-commits",
        "--no-gitignore",
        "--no-show-model-warnings",
        "--chat-mode", "ask",   # 파일 수정 없이 질의응답만
        "--exit",
        "--yes",
        "--message", prompt,
    ]

    if file_paths:
        for fp in file_paths:
            full_path = os.path.join(workspace_path, fp)
            if os.path.exists(full_path):
                aider_command.extend(["--file", fp])
            else:
                logger.debug(f"[MR #{mr_iid}] --file 스킵 (없음): {fp}")

    log_cmd = [a if prev != "--message" else "<prompt>" for prev, a in zip([""] + aider_command, aider_command)]
    logger.info(f"🚀 [MR #{mr_iid}] 실행 명령어: {' '.join(log_cmd)}")

    try:
        process = subprocess.run(
            aider_command,
            cwd=workspace_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=settings.aider_timeout,
        )

        stdout = _normalize_markdown(_extract_llm_response(_strip_box_drawing(_strip_ansi(process.stdout))))
        stderr = _strip_ansi(process.stderr)

        if _CONNECTION_ERROR_PATTERNS.search(stdout) or _CONNECTION_ERROR_PATTERNS.search(stderr):
            logger.error(f"❌ [MR #{mr_iid}] LLM 연결 실패 (exit 0이지만 오류 감지):\n{stderr or stdout}")
            return None

        if process.returncode != 0:
            logger.error(f"❌ [MR #{mr_iid}] Aider 실행 에러 (exit {process.returncode}): {stderr}")
            return None

        return stdout or None
    except subprocess.TimeoutExpired:
        logger.error(f"❌ [MR #{mr_iid}] Aider 타임아웃 ({settings.aider_timeout}초 초과)")
        return None
    except Exception as e:
        logger.error(f"⚠️ [MR #{mr_iid}] Aider 실행 중 예외 발생: {str(e)}")
        return None
