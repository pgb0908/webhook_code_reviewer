import logging
from typing import Optional

from ai.shared.subprocess import run_aider_subprocess
from ai.shared.output import parse_yaml_safe, render_comment_markdown

logger = logging.getLogger(__name__)


def _build_user_ask_prompt(question: Optional[str]) -> str:
    user_question = question if question else "이 Merge Request의 변경 사항에 대해 전반적인 코드 리뷰를 해줘."

    return f"""[역할]
우리 팀 수석 SRE이자 C++ 전문가.

⚠️ 중요: 모든 텍스트 값을 반드시 한국어(한글)로만 작성하라. 영어 사용 절대 금지.

[질문]
{user_question}

[답변 규칙]
- 결론부터 시작한다 (질문 반복 금지)
- Repo Map의 실제 파일명·함수명을 근거로 인용한다
- 코드 예시는 cpp 블록 사용, 수정 제안은 before/after로 표현한다
- before/after 필드에는 순수 코드만 넣는다. 설명 문장 혼입 금지
- 리뷰 포인트 심각도: critical | warning | suggestion
- 확실하지 않으면 "(추측)" 명시
- 코드를 직접 수정하지 말고 제안만 한다

[⚠️ 출력 규칙 — 반드시 준수]
- 아래 ```yaml 펜스 하나만 출력한다
- 펜스 밖에 설명·인사·부연을 절대 추가하지 않는다
- conclusion, analysis, description의 모든 값은 반드시 한국어(한글)로만 작성한다. 영어 금지.

```yaml
conclusion: |
  <결론 요약 1~3문장 — 반드시 한국어로>
analysis: |
  <상세 분석 — 반드시 한국어로. 단순 질문이면 이 키 생략>
suggestions:
  - severity: warning
    description: <제안 설명 — 반드시 한국어로>
    file: <파일명, 없으면 생략>
    before: |
      <기존 코드만. 설명 문장 금지>
    after: |
      <개선된 코드만. 설명 문장 금지>
```

모든 텍스트 값(conclusion, analysis, description)은 한국어로 작성할 것. 영어 사용 금지.
"""


def run_aider_comment(
        mr_iid: str,
        workspace_path: str,
        question: Optional[str],
) -> Optional[str]:
    """Aider CLI를 실행하여 응답 텍스트를 반환한다. 실패 시 None."""
    logger.info(f"🧠 [MR #{mr_iid}] 질문에 대한 응답 생성 중...")
    prompt = _build_user_ask_prompt(question)
    raw = run_aider_subprocess(mr_iid, workspace_path, prompt)
    if raw is None:
        return None

    data = parse_yaml_safe(raw)
    if data and "conclusion" in data:
        return render_comment_markdown(data)

    # Fallback: YAML 실패 시 raw 텍스트 그대로 반환
    logger.warning(f"⚠️ [MR #{mr_iid}] comment YAML 파싱 실패, raw 텍스트 반환")
    return raw
