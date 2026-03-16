# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.

import logging
import re
from typing import Optional

from aider_bot.ai.aider import run_aider_subprocess
from aider_bot.ai.llm_client import chat_completion
from aider_bot.ai.output import parse_structured_output

logger = logging.getLogger(__name__)

_HANGUL_RE = re.compile(r"[가-힣]")

_STRUCTURING_SYSTEM_PROMPT = """당신의 역할은 코드리뷰 응답을 지정된 태그 스키마로 정규화하는 구조화 엔진이다.
- 반드시 지정된 루트 태그 하나만 출력한다
- 태그 밖 설명, 인사, 마크다운, 코드펜스, JSON, YAML을 출력하지 않는다
- conclusion, analysis, summary, description 등 설명 텍스트는 반드시 한국어로 쓴다
- 불명확한 내용은 과장하지 말고 '(추측)'을 붙이거나 빈 값으로 둔다
- before/after에는 순수 코드만 넣는다
- before/after의 각 줄은 4칸 이상 들여쓰기를 유지한다
- 코드 제안이 불명확하면 LANGUAGE/BEFORE/AFTER는 생략한다"""


def _schema_example(schema: str) -> str:
    examples = {
        "comment": """<COMMENT>
<CONCLUSION>결론</CONCLUSION>
<ANALYSIS>상세 분석</ANALYSIS>
<SUGGESTION>
<SEVERITY>warning</SEVERITY>
<FILE>src/example.py</FILE>
<DESCRIPTION>설명</DESCRIPTION>
<LANGUAGE>python</LANGUAGE>
<BEFORE>
    old_code()
</BEFORE>
<AFTER>
    new_code()
</AFTER>
</SUGGESTION>
</COMMENT>""",
        "overview": """<OVERVIEW>
<TITLE>제목</TITLE>
<SUMMARY>요약</SUMMARY>
<FILE_CHANGE>
<FILE>src/example.py</FILE>
<CHANGE>변경점</CHANGE>
<LANGUAGE>python</LANGUAGE>
<BEFORE>
    old_code()
</BEFORE>
<AFTER>
    new_code()
</AFTER>
</FILE_CHANGE>
<REVIEW_POINT>
<SEVERITY>warning</SEVERITY>
<DESCRIPTION>리뷰 포인트</DESCRIPTION>
<FILE>src/example.py</FILE>
</REVIEW_POINT>
</OVERVIEW>""",
        "unit_review": """<UNIT_REVIEW>
<FINDING>
<SEVERITY>warning</SEVERITY>
<TITLE>제목</TITLE>
<DESCRIPTION>설명</DESCRIPTION>
<FILE>src/example.py</FILE>
<LINES>10-18</LINES>
<CONFIDENCE>medium</CONFIDENCE>
</FINDING>
</UNIT_REVIEW>""",
    }
    return examples[schema]


def _schema_rules(schema: str) -> str:
    rules = {
        "comment": """- COMMENT 루트를 사용한다
- CONCLUSION과 ANALYSIS는 반드시 채운다
- SUGGESTION은 0개 이상이다
- SEVERITY는 critical | warning | suggestion 중 하나만 사용한다
- 문제 지점이 명확하면 FILE을 반드시 채운다
- 최소 수정 예시를 제시할 수 있으면 LANGUAGE/BEFORE/AFTER를 적극 채운다
- 코드 제안이 없으면 LANGUAGE/BEFORE/AFTER를 생략한다""",
        "overview": """- OVERVIEW 루트를 사용한다
- TITLE은 한국어 40자 이내로 작성한다
- SUMMARY는 3~5문장으로 작성한다
- FILE_CHANGE와 REVIEW_POINT는 0개 이상이다
- SEVERITY는 critical | warning | suggestion 중 하나만 사용한다
- 주요 변경 근거를 보여줄 수 있으면 FILE_CHANGE에 LANGUAGE/BEFORE/AFTER를 채운다
- 코드 근거가 불명확하면 LANGUAGE/BEFORE/AFTER를 생략한다""",
        "unit_review": """- UNIT_REVIEW 루트를 사용한다
- FINDING은 0개 이상이다
- 실제 버그/회귀/보안/검증 누락만 남긴다
- 근거가 약하면 FINDING을 만들지 않는다
- SEVERITY는 critical | warning | suggestion 중 하나만 사용한다
- CONFIDENCE는 high | medium | low 중 하나만 사용한다""",
    }
    return rules[schema]


def _build_structuring_prompt(schema: str, raw_text: str) -> str:
    return f"""[작업]
아래 코드리뷰 자유형 응답을 지정된 태그 스키마로 정규화하라.

[스키마 규칙]
{_schema_rules(schema)}

[출력 예시]
{_schema_example(schema)}

[대상 응답]
```text
{raw_text}
```"""


def _build_retry_prompt(schema: str, raw_text: str) -> str:
    return f"""[재시도 작업]
이전 응답이 스키마를 지키지 않았거나 영어가 섞였다.
이번에는 반드시 한국어 태그 스키마만 출력하라.

[필수 조건]
{_schema_rules(schema)}
- conclusion, analysis, summary, description, title은 반드시 한국어만 사용한다
- 영어 문장을 복사하지 말고 한국어로 번역·요약한다
- 태그 밖 텍스트를 절대 출력하지 않는다
- 정보가 부족하면 빈 값으로 둔다

[출력 예시]
{_schema_example(schema)}

[대상 응답]
```text
{raw_text}
```"""


def _contains_hangul(text: str) -> bool:
    return bool(_HANGUL_RE.search(str(text or "")))


def _is_korean_structured(data: dict, schema: str) -> bool:
    if schema == "comment":
        return _contains_hangul(data.get("conclusion")) and _contains_hangul(data.get("analysis"))
    if schema == "overview":
        return _contains_hangul(data.get("title")) and _contains_hangul(data.get("summary"))
    if schema == "unit_review":
        findings = data.get("findings", []) or []
        return not findings or any(_contains_hangul(item.get("description")) for item in findings if isinstance(item, dict))
    return True


def structure_review_output(
    mr_iid: str,
    raw_text: str,
    schema: str,
) -> tuple[Optional[dict], Optional[str]]:
    direct = parse_structured_output(raw_text, schema=schema)
    if direct is not None:
        return direct, raw_text

    structured_raw = chat_completion(
        mr_iid,
        _STRUCTURING_SYSTEM_PROMPT,
        _build_structuring_prompt(schema, raw_text),
    )
    if structured_raw is None:
        return None, None

    structured = parse_structured_output(structured_raw, schema=schema)
    if structured is not None and _is_korean_structured(structured, schema):
        logger.info("ℹ️ [MR #%s] %s llm-client 구조화 성공", mr_iid, schema)
        return structured, structured_raw

    retry_raw = chat_completion(
        mr_iid,
        _STRUCTURING_SYSTEM_PROMPT,
        _build_retry_prompt(schema, raw_text),
    )
    if retry_raw is None:
        return None, structured_raw

    retry_structured = parse_structured_output(retry_raw, schema=schema)
    if retry_structured is not None and _is_korean_structured(retry_structured, schema):
        logger.info("ℹ️ [MR #%s] %s llm-client 구조화 재시도 성공", mr_iid, schema)
        return retry_structured, retry_raw

    logger.warning("⚠️ [MR #%s] %s llm-client 구조화 실패", mr_iid, schema)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("🧾 [MR #%s] %s llm-client 구조화 응답:\n%s", mr_iid, schema, structured_raw)
        if retry_raw is not None:
            logger.debug("🧾 [MR #%s] %s llm-client 구조화 재시도 응답:\n%s", mr_iid, schema, retry_raw)
    return None, retry_raw or structured_raw


def run_aider_and_structure(
    mr_iid: str,
    workspace_path: str,
    prompt: str,
    schema: str,
    files: list[str] | None = None,
) -> tuple[Optional[dict], Optional[str]]:
    raw = run_aider_subprocess(mr_iid, workspace_path, prompt, files=files)
    if raw is None:
        return None, None

    structured, structured_raw = structure_review_output(mr_iid, raw, schema=schema)
    fallback_text = structured_raw or raw
    return structured, fallback_text
