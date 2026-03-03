import logging
import re
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_YAML_FENCE_RE = re.compile(r"```(?:yaml|YAML)\s*\n(.*?)```", re.DOTALL)
_BLOCK_SCALAR_LINE_RE = re.compile(r"^(\s*)[\w\-]+\s*:\s*[|>][+\-]?\s*$")
_YAML_KEY_LINE_RE = re.compile(r"^(\s*)[\w\-]+\s*:")

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


def extract_yaml_block(text: str) -> Optional[str]:
    """LLM 출력에서 YAML 블록을 추출한다.

    1순위: ```yaml ... ``` 펜스 탐색
    2순위: 'title:' 또는 'conclusion:' 으로 시작하는 줄부터 끝까지 추출
    실패 시 None
    """
    match = _YAML_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()

    # 2순위: 최상위 YAML 키로 시작하는 줄 탐색
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("title:") or stripped.startswith("conclusion:"):
            return "\n".join(lines[i:]).strip()

    return None


def parse_yaml_safe(text: str) -> Optional[dict]:
    """LLM 출력 텍스트에서 YAML을 파싱하여 dict를 반환한다. 실패 시 None."""
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

    # 2차 시도: block scalar 들여쓰기 보정 후 재파싱
    repaired = _repair_yaml_block_scalars(block)
    try:
        data = yaml.safe_load(repaired)
        if isinstance(data, dict):
            logger.info("ℹ️ parse_yaml_safe: block scalar 보정 후 파싱 성공")
            return data
        logger.warning(f"⚠️ parse_yaml_safe: YAML 파싱 결과가 dict가 아님 ({type(data).__name__})")
        return None
    except yaml.YAMLError as e:
        logger.warning(f"⚠️ parse_yaml_safe: YAML 파싱 오류 — {e}")
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

            if before:
                suggestion_parts.append(f"\n**Before**\n```cpp\n{str(before).strip()}\n```")
            if after:
                suggestion_parts.append(f"**After**\n```cpp\n{str(after).strip()}\n```")

        parts.append("\n".join(suggestion_parts))

    return "\n\n".join(parts)
