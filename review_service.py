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


def _run_aider_subprocess(settings, mr_iid: str, workspace_path: str, prompt: str) -> Optional[str]:
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
            timeout=settings.aider_timeout,
        )

        stdout = _strip_box_drawing(_strip_ansi(process.stdout))
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


def run_aider_review(settings, mr_iid: str, workspace_path: str, diff_result: DiffResult, question: Optional[str]) -> Optional[str]:
    """Aider CLI를 실행하여 코드 리뷰 결과를 반환한다. 실패 시 None 반환."""
    logger.info(f"🧠 [MR #{mr_iid}] Aider 분석 및 답변 생성 중...")
    prompt = _build_prompt(diff_result, question)
    return _run_aider_subprocess(settings, mr_iid, workspace_path, prompt)


def _build_overview_prompt(diff_result: DiffResult, original_title: str) -> str:
    truncation_warning = ""
    if diff_result.truncated:
        truncation_warning = (
            f"\n⚠️ **주의**: Diff가 너무 커서 앞부분 {len(diff_result.content)}자만 포함되었습니다 "
            f"(전체 {diff_result.original_length}자).\n"
        )

    return f"""
    너는 우리 팀의 수석 SRE이자 C++ 백엔드 전문가야.
    아래 Merge Request의 diff를 분석해서 MR 보고서를 작성해줘.

    원래 MR 제목: {original_title}
    {truncation_warning}
    아래는 이번 Merge Request의 코드 변경 사항(Diff)이야:
    ```diff
    {diff_result.content}
    ```

    **반드시 아래 형식으로만 답변해:**

    TITLE: <diff를 한 줄로 요약한 MR 제목>
    ---
    > 🤖 이 설명은 Aider AI가 자동 생성했습니다.

    ## 📋 주요 변경 사항
    (가장 의미있는 변경부터 번호 목록으로 작성)

    ## 💬 코드 리뷰
    (코드 품질, 잠재 버그, 개선 제안을 마크다운으로 작성)

    ---
    *Aider AI Code Review Bot 자동 생성*

    코드는 절대 직접 수정하지 마. TITLE: 줄과 --- 구분자를 반드시 포함해야 해.
    """


def parse_overview_output(raw: str) -> tuple[str, str]:
    """aider 출력에서 TITLE과 description을 파싱한다."""
    lines = raw.strip().splitlines()
    title = ""
    desc_lines = []
    separator_found = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not title and stripped.startswith("TITLE:"):
            title = stripped[len("TITLE:"):].strip()
            continue
        if title and not separator_found and stripped == "---":
            separator_found = True
            desc_lines = lines[i + 1:]
            break

    if title and separator_found:
        return title, "\n".join(desc_lines).strip()

    # fallback
    logger.warning("⚠️ parse_overview_output: TITLE/--- 포맷 감지 실패, fallback 사용")
    return "AI 코드 리뷰 완료", raw


def run_aider_overview(
    settings,
    mr_iid: str,
    workspace_path: str,
    diff_result: DiffResult,
    original_title: str,
) -> Optional[tuple[str, str]]:
    """Aider CLI를 실행하여 (title, description) 튜플을 반환한다. 실패 시 None."""
    logger.info(f"🧠 [MR #{mr_iid}] Aider MR overview 보고서 생성 중...")
    prompt = _build_overview_prompt(diff_result, original_title)
    raw = _run_aider_subprocess(settings, mr_iid, workspace_path, prompt)
    if raw is None:
        return None
    return parse_overview_output(raw)
