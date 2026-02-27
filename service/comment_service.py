import logging
from typing import Optional

from service.common.aider_subprocess import _run_aider_subprocess

logger = logging.getLogger(__name__)


def _build_user_ask_prompt(question: Optional[str]) -> str:
    user_question = question if question else "이 Merge Request의 변경 사항에 대해 전반적인 코드 리뷰를 해줘."

    return f"""[역할]
우리 팀 수석 SRE이자 C++ 백엔드 전문가. 아래 질문에 한글로 답하라.

[질문]
{user_question}

[답변 규칙]
- 질문을 반복하지 말고 결론부터 시작한다
- Repo Map의 실제 파일명·함수명을 근거로 인용한다
- 코드 예시는 ```cpp 블록을 사용한다
- 단순 질문은 단락 1~2개(150단어 이내)로, 복잡한 분석은 ## 결론 / ## 상세 구조를 사용한다
- 확실하지 않으면 "(추측)" 이라고 명시한다
- 코드를 직접 수정하지 말고 제안만 한다
"""


def run_aider_comment(
        settings,
        mr_iid: str,
        workspace_path: str,
        question: Optional[str],
) -> Optional[str]:
    """Aider CLI를 실행하여 응답 텍스트를 반환한다. 실패 시 None."""
    logger.info(f"🧠 [MR #{mr_iid}] 질문에 대한 응답 생성 중...")
    prompt = _build_user_ask_prompt(question)
    return _run_aider_subprocess(settings, mr_iid, workspace_path, prompt)
