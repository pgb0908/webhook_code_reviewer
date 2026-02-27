import os
import re
import logging
import subprocess
from typing import Optional

from git_service import DiffResult

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


def _build_prompt(diff_result: DiffResult, question: Optional[str]) -> str:
    user_question = question if question else "이 Merge Request의 변경 사항에 대해 전반적인 코드 리뷰를 해줘."

    truncation_warning = ""
    if diff_result.truncated:
        truncation_warning = (
            f"\n⚠️ **주의**: Diff가 너무 커서 앞부분 {len(diff_result.content)}자만 포함되었습니다 "
            f"(전체 {diff_result.original_length}자).\n"
        )

    return f"""
    너는 우리 팀의 수석 SRE이자 C++ 백엔드 전문가야.
    {user_question}
    {truncation_warning}
    아래는 이번 Merge Request의 코드 변경 사항(Diff)이야:
    ```diff
    {diff_result.content}
    ```

    기존 프로젝트의 Repo Map과 위 변경 사항을 종합적으로 분석해서 명확하게 마크다운으로 답변해 줘.
    코드는 절대 직접 수정하지 마.
    """


def run_aider_review(settings, mr_iid: str, workspace_path: str, diff_result: DiffResult, question: Optional[str]) -> Optional[str]:
    """Aider CLI를 실행하여 코드 리뷰 결과를 반환한다. 실패 시 None 반환."""
    logger.info(f"🧠 [MR #{mr_iid}] Aider 분석 및 답변 생성 중...")
    prompt = _build_prompt(diff_result, question)

    env = os.environ.copy()
    env["OPENAI_API_BASE"] = settings.remote_llm_base_url
    env["OPENAI_API_KEY"] = settings.remote_llm_api_key

    aider_command = [
        "aider",
        "--model", settings.aider_model,
        "--api-key", "openai=none",
        "--no-auto-commits",
        "--no-gitignore",           # .gitignore 자동 수정 방지
        "--no-show-model-warnings", # 모델 경고 메시지 억제
        "--exit",
        "--yes",
        "--message", prompt,
    ]

    logger.info(f"🚀 [MR #{mr_iid}] 실행 명령어: {' '.join(aider_command)}")

    try:
        process = subprocess.run(
            aider_command,
            cwd=workspace_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=settings.aider_timeout,  # 600초 = 10분
        )

        stdout = _strip_box_drawing(_strip_ansi(process.stdout))
        stderr = _strip_ansi(process.stderr)

        # exit code 0이어도 LLM 연결 실패인 경우 감지
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
