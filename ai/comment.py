# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import logging
from typing import Optional

from ai.shared.subprocess import run_aider_subprocess
from ai.shared.output import parse_yaml_safe, render_comment_markdown, render_raw_fallback
from git.diff import rank_changed_files

logger = logging.getLogger(__name__)


def _build_user_ask_prompt(question: Optional[str], diff_content: Optional[str] = None) -> str:
    user_question = question if question else "이 Merge Request의 변경 사항에 대해 전반적인 코드 리뷰를 해줘."

    diff_section = ""
    if diff_content and diff_content.strip():
        diff_section = f"""[Diff]
```diff
{diff_content}
```

"""

    return f"""[역할]
우리 팀 수석 SRE이자 백엔드 전문가.

⚠️ 중요: 모든 텍스트 값을 반드시 한국어(한글)로만 작성하라. 영어 사용 절대 금지.

{diff_section}[질문]
{user_question}

[답변 규칙]
- 결론부터 시작한다 (질문 반복 금지)
- Repo Map과 제공된 Diff를 근거로 답변한다
- Repo Map의 실제 파일명·함수명을 근거로 인용한다
- 수정 제안은 before/after로 표현한다
- before/after 필드에는 순수 코드만 넣는다. 설명 문장 혼입 금지
- before/after 코드의 각 줄은 반드시 4칸 이상 들여쓰기를 유지한다
- 코드에 `:`, `#`, `{{{{`, `[` 등이 포함되어도 4칸 들여쓰기만 지키면 안전하다
- language 필드는 코드 블록 언어를 명시한다 (java, python, cpp 등)
- 리뷰 포인트 심각도: critical | warning | suggestion
- 확실하지 않으면 "(추측)" 명시
- 코드를 직접 수정하지 말고 제안만 한다
- 값에 콜론(:)이 포함되면 큰따옴표로 감싸라 (예: description: "예: 위험한 패턴")

[⚠️ 출력 규칙 — 반드시 준수]
- 아래 ```yaml 펜스 하나만 출력한다
- 펜스 밖에 설명·인사·부연을 절대 추가하지 않는다 — 펜스 밖 텍스트는 파싱 실패의 원인이 된다
- conclusion, analysis, description의 모든 값은 반드시 한국어(한글)로만 작성한다. 영어 금지.

[올바른 출력 예시]
```yaml
conclusion: |
  널 포인터 역참조 위험이 있으며 즉시 수정이 필요합니다.
analysis: |
  getData() 함수가 null을 반환할 수 있으나 호출부에서 검증하지 않습니다.
suggestions:
  - severity: warning
    description: "널 체크 누락(예: getData 반환값)"
    file: server.cpp
    language: cpp
    before: |
      auto data = getData();
      data->process();
    after: |
      auto data = getData();
      if (data) data->process();
  - severity: suggestion
    description: 로그 레벨을 조정하라
    file: logger.py
```

```yaml
conclusion: |
  <결론 요약 1~3문장 — 반드시 한국어로>
analysis: |
  <상세 분석 — 반드시 한국어로. 단순 질문이면 이 키 생략>
suggestions:
  # 케이스 A — 코드 수정 제안이 명확할 때만 before/after 포함
  - severity: warning
    description: "위험한 패턴 발견"
    file: <파일명, 없으면 생략>
    language: <before/after 코드의 언어 식별자 (예: java, python, cpp). 코드가 없으면 생략>
    before: |
      <기존 코드만. 설명 문장 금지. 4칸 들여쓰기 필수>
    after: |
      <개선된 코드만. 설명 문장 금지. 4칸 들여쓰기 필수>
  # 케이스 B — 코드 수정 없이 지적·안내만 필요할 때 before/after 생략
  - severity: suggestion
    description: <제안 설명 — 반드시 한국어로>
    file: <파일명, 없으면 생략>
```

모든 텍스트 값(conclusion, analysis, description)은 한국어로 작성할 것. 영어 사용 금지.
`before`/`after`는 실제로 다른 코드로 수정할 수 있을 때만 포함한다. 내용이 동일하거나 변경이 불명확하면 생략한다.
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
    files = rank_changed_files(diff_content) if diff_content else []
    raw = run_aider_subprocess(mr_iid, workspace_path, prompt, files=files)
    if raw is None:
        return None

    data = parse_yaml_safe(raw, schema="comment")
    if data and "conclusion" in data:
        return render_comment_markdown(data)

    # Fallback: YAML 실패 시 정리된 raw 텍스트 반환
    logger.warning(f"⚠️ [MR #{mr_iid}] comment YAML 파싱 실패, raw 폴백 반환")
    return render_raw_fallback(raw)
