# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import logging
import re
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_YAML_FENCE_RE = re.compile(r"```(?:yaml|YAML)\s*\n(.*?)```", re.DOTALL)
_BLOCK_SCALAR_LINE_RE = re.compile(r"^(\s*)[\w\-]+\s*:\s*[|>][+\-]?\s*$")
_YAML_KEY_LINE_RE = re.compile(r"^(\s*)[\w\-]+\s*:")
_YAML_KEY_VALUE_RE = re.compile(r"^(\s*)([\w\-]+)\s*:\s*(.*)")
_YAML_SEQ_LINE_RE = re.compile(r"^\s*-\s")
_YAML_DOC_LINE_RE = re.compile(r"^(---|\.\.\.)$")
_PLAIN_SCALAR_COLON_RE = re.compile(r"^(\s*[\w\-]+\s*:\s*)(.+)$")

_SEVERITY_EMOJI = {
    "critical": "🔴",
    "warning": "🟡",
    "suggestion": "🟢",
}


def _severity_label(severity: str) -> str:
    key = severity.lower() if severity else ""
    emoji = _SEVERITY_EMOJI.get(key, "🟢")
    label = severity.capitalize() if severity else "Suggestion"
    return f"{emoji} **{label}**"


def _repair_yaml_block_scalars(text: str) -> str:
    """LLM이 block scalar(| 또는 >) 내용을 들여쓰기 없이 출력한 경우를 보정한다."""
    lines = text.splitlines()
    result = []
    in_block = False
    block_key_indent = 0
    min_content_indent = 2

    for line in lines:
        stripped = line.strip()

        if in_block:
            if not stripped:
                result.append(line)
                continue

            key_match = _YAML_KEY_LINE_RE.match(line)
            if key_match:
                current_indent = len(key_match.group(1))
                if current_indent <= block_key_indent:
                    in_block = False
                    result.append(line)
                    m = _BLOCK_SCALAR_LINE_RE.match(line)
                    if m:
                        in_block = True
                        block_key_indent = len(m.group(1))
                        min_content_indent = block_key_indent + 2
                    continue

            # 들여쓰기가 부족한 content 줄 처리
            current_line_indent = len(line) - len(line.lstrip())
            if current_line_indent < min_content_indent:
                if block_key_indent == 0:
                    # 최상위 블록(summary 등): 들여쓰기 보정
                    result.append(" " * min_content_indent + stripped)
                else:
                    # 중첩 블록(before/after 등): column 0 줄은 aider 자체 설명
                    # 이므로 버리고 블록 종료 (파싱 오염 방지)
                    in_block = False
            else:
                result.append(line)
        else:
            result.append(line)
            m = _BLOCK_SCALAR_LINE_RE.match(line)
            if m:
                in_block = True
                block_key_indent = len(m.group(1))
                min_content_indent = block_key_indent + 2

    return "\n".join(result)


def _repair_wrapped_scalars(text: str) -> str:
    """LLM이 plain scalar 값을 줄바꿈하여 들여쓰기 없이(col 0) 이어 쓴 경우를 수정한다.

    예:
        description: 긴 설명 텍스트가 줄바꿈되어
경우에도 이어지는 텍스트입니다.
        file: server.cpp

    →   description: 긴 설명 텍스트가 줄바꿈되어 경우에도 이어지는 텍스트입니다.
        file: server.cpp
    """
    lines = text.splitlines()
    result = []
    last_plain_value_indent = -1  # 마지막으로 plain scalar 값을 가진 키의 들여쓰기 수준

    for line in lines:
        stripped = line.rstrip()

        if not stripped:
            result.append(stripped)
            last_plain_value_indent = -1
            continue

        current_indent = len(stripped) - len(stripped.lstrip())

        # 이전 키보다 들여쓰기가 작고, 새로운 YAML 구조 요소가 아니면 → 이어쓰기 줄
        if (last_plain_value_indent >= 0
                and current_indent < last_plain_value_indent
                and not _YAML_KEY_LINE_RE.match(stripped)
                and not _YAML_SEQ_LINE_RE.match(stripped)
                and not _YAML_DOC_LINE_RE.match(stripped)
                and not stripped.lstrip().startswith("#")):
            if result:
                result[-1] = result[-1] + " " + stripped.lstrip()
            continue  # last_plain_value_indent 유지 (연속 이어쓰기 지원)

        result.append(stripped)

        km = _YAML_KEY_VALUE_RE.match(stripped)
        if km:
            key_indent = len(km.group(1))
            value = km.group(3).strip()
            # block scalar(| >) 또는 값 없는 키는 이어쓰기 탐지 대상 아님
            if value and not value[0] in ("|", ">", "{", "["):
                last_plain_value_indent = key_indent
            else:
                last_plain_value_indent = -1
        else:
            last_plain_value_indent = -1

    return "\n".join(result)


def _quote_colon_in_plain_scalars(text: str) -> str:
    """plain scalar 값에 ': ' 가 포함된 경우 YAML 파싱 오류를 방지하기 위해 큰따옴표로 감싼다.

    YAML block context에서 plain scalar 안의 ': ' (콜론+공백)는 새 key-value 시작으로
    해석되어 파싱이 깨진다. 예: '형식(예: 헤더, 쿠키 등)' → '"형식(예: 헤더, 쿠키 등)"'
    block scalar(|, >), 이미 따옴표로 감싼 값, flow indicator({, [)는 건드리지 않는다.
    """
    lines = text.splitlines()
    result = []
    for line in lines:
        m = _PLAIN_SCALAR_COLON_RE.match(line)
        if m:
            key_part = m.group(1)
            value = m.group(2).strip()
            if (": " in value or value.endswith(":")) and value[0] not in ("|", ">", '"', "'", "{", "["):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                result.append(f'{key_part}"{escaped}"')
                continue
        result.append(line)
    return "\n".join(result)


_YAML_OPEN_FENCE_RE = re.compile(r"```(?:yaml|YAML)\s*\n(.*)", re.DOTALL)


def extract_yaml_block(text: str) -> Optional[str]:
    """LLM 출력에서 YAML 블록을 추출한다.

    1순위: ```yaml ... ``` 펜스 탐색
    2순위: 닫힌 펜스 없이 ```yaml 이후 끝까지 추출
    3순위: 'title:' 또는 'conclusion:' 으로 시작하는 줄부터 끝까지 추출
    실패 시 None
    """
    match = _YAML_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()

    # 2순위: 닫힌 펜스 없이 열린 펜스만 있는 경우
    open_match = _YAML_OPEN_FENCE_RE.search(text)
    if open_match:
        content = open_match.group(1)
        lines = content.splitlines()
        result = []
        for line in lines:
            if line.strip().startswith("```"):
                break
            result.append(line)
        if result:
            return "\n".join(result).strip()

    # 3순위: 최상위 YAML 키로 시작하는 줄 탐색
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("title:") or stripped.startswith("conclusion:"):
            return "\n".join(lines[i:]).strip()

    return None


def parse_yaml_safe(text: str) -> Optional[dict]:
    """LLM 출력 텍스트에서 YAML을 파싱하여 dict를 반환한다. 실패 시 None."""
    logger.debug(f"디버깅 텍스트: {text}")

    block = extract_yaml_block(text)
    if block is None:
        logger.warning("⚠️ parse_yaml_safe: YAML 블록을 찾지 못함")
        return None

    # 1차 시도: 원본 그대로 파싱
    try:
        data = yaml.safe_load(block)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass

    # 2차 시도: wrapped scalar + block scalar 보정 후 재파싱
    repaired = _repair_wrapped_scalars(_repair_yaml_block_scalars(block))
    try:
        data = yaml.safe_load(repaired)
        if isinstance(data, dict):
            logger.info("ℹ️ parse_yaml_safe: 보정 후 파싱 성공")
            return data
    except yaml.YAMLError:
        pass

    # 3차 시도: plain scalar 내 ': ' 큰따옴표 이스케이프 후 재파싱
    # 한국어 텍스트의 '예: 헤더' 같은 패턴이 YAML 키로 오해되는 문제 해결
    quoted = _quote_colon_in_plain_scalars(repaired)
    try:
        data = yaml.safe_load(quoted)
        if isinstance(data, dict):
            logger.info("ℹ️ parse_yaml_safe: colon 이스케이프 후 파싱 성공")
            return data
    except yaml.YAMLError:
        pass

    # 4차 시도: 빈 줄 기준으로 뒤에서부터 잘라내며 재파싱
    # (AI가 YAML 뒤에 영문 설명·중복 YAML을 추가했을 때 YAML 부분만 추출)
    segments = re.split(r'\n\s*\n', quoted)
    for n in range(len(segments) - 1, 0, -1):
        truncated = '\n\n'.join(segments[:n])
        try:
            data = yaml.safe_load(truncated)
            if isinstance(data, dict) and ("title" in data or "conclusion" in data):
                logger.info(f"ℹ️ parse_yaml_safe: 후미 텍스트 제거 후 파싱 성공 (segments={n}/{len(segments)})")
                return data
        except yaml.YAMLError:
            pass

    logger.warning("⚠️ parse_yaml_safe: YAML 파싱 최종 실패\n--- block ---\n%s\n---", block)
    return None


def render_overview_markdown(data: dict) -> tuple[str, str]:
    """YAML dict를 overview (title, description) 마크다운으로 렌더링한다."""
    title = str(data.get("title", "")).strip()

    sections = ["> 🤖 이 설명은 Aider AI가 자동 생성했습니다.\n"]

    summary = data.get("summary")
    if summary:
        sections.append(f"## 📋 변경 개요\n{str(summary).strip()}")

    file_changes = data.get("file_changes")
    if file_changes and isinstance(file_changes, list):
        rows = []
        for idx, fc in enumerate(file_changes, 1):
            if not isinstance(fc, dict):
                continue
            fname = fc.get("file", "")
            change = fc.get("change", "")
            rows.append(f"| {idx} | `{fname}` | {change} |")
        if rows:
            table = "## 🔍 주요 변경 사항\n\n| # | 파일 | 변경 내용 |\n|---|------|-----------|"
            sections.append(table + "\n" + "\n".join(rows))

    review_points = data.get("review_points")
    if review_points and isinstance(review_points, list):
        bullets = []
        for rp in review_points:
            if not isinstance(rp, dict):
                continue
            severity = rp.get("severity", "suggestion")
            desc = rp.get("description", "")
            file_ref = rp.get("file", "")
            label = _severity_label(severity)
            bullet = f"- {label}: {desc}"
            if file_ref:
                bullet += f" (`{file_ref}`)"
            bullets.append(bullet)
        if bullets:
            sections.append("## ⚠️ 리뷰 포인트\n" + "\n".join(bullets))

    sections.append("---\n<sub>🤖 Aider AI Code Review Bot 자동 생성</sub>")

    description = "\n\n".join(sections)
    return title, description


def render_comment_markdown(data: dict) -> str:
    """YAML dict를 comment 마크다운으로 렌더링한다."""
    parts = []

    conclusion = data.get("conclusion")
    if conclusion:
        parts.append(f"## 💡 결론\n{str(conclusion).strip()}")

    analysis = data.get("analysis")
    if analysis:
        parts.append(f"## 🔍 상세 분석\n{str(analysis).strip()}")

    suggestions = data.get("suggestions")
    if suggestions and isinstance(suggestions, list):
        suggestion_parts = ["## 🛠️ 개선 제안"]
        for sg in suggestions:
            if not isinstance(sg, dict):
                continue
            severity = sg.get("severity", "suggestion")
            desc = sg.get("description", "")
            file_ref = sg.get("file", "")
            before = sg.get("before")
            after = sg.get("after")

            label = _severity_label(severity)
            line = f"\n{label} **—** {desc}"
            if file_ref:
                line += f"\n`{file_ref}`"
            suggestion_parts.append(line)

            before_str = str(before).strip() if before else ""
            after_str = str(after).strip() if after else ""
            if before_str and after_str and before_str != after_str:
                lang_fence = str(sg.get("language", "")).strip()
                suggestion_parts.append(f"\n**Before**\n```{lang_fence}\n{before_str}\n```")
                suggestion_parts.append(f"**After**\n```{lang_fence}\n{after_str}\n```")

        parts.append("\n".join(suggestion_parts))

    return "\n\n".join(parts)
