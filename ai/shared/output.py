# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import json
import logging
import re
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_YAML_FENCE_RE = re.compile(r"```(?:yaml|YAML)\s*\n(.*?)```", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)\s*\n(.*?)```", re.DOTALL)
_BLOCK_SCALAR_LINE_RE = re.compile(r"^(\s*)[\w\-]+\s*:\s*[|>][+\-]?\s*$")
_YAML_KEY_LINE_RE = re.compile(r"^(\s*)[\w\-]+\s*:")
_YAML_KEY_VALUE_RE = re.compile(r"^(\s*)([\w\-]+)\s*:\s*(.*)")
_YAML_SEQ_LINE_RE = re.compile(r"^\s*-\s")
_YAML_DOC_LINE_RE = re.compile(r"^(---|\.\.\.)$")
_PLAIN_SCALAR_COLON_RE = re.compile(r"^(\s*[\w\-]+\s*:\s*)(.+)$")
_SEQ_ITEM_KV_RE = re.compile(r"^(\s*-\s+)([\w\-]+\s*:\s*)(.+)$")

# aider 메타 라인 패턴 (raw 폴백 정리용)
_AIDER_META_LINE = re.compile(
    r"^(Aider v|Model:|Git repo:|Repo-map:|Added |Tokens:|Applied edit to|"
    r"Only \d+ reflections|Summarization failed|can't summarize|"
    r"Auto-committing|Warning:|No changes made|"
    r"[A-Za-z0-9_./-]+\.[a-zA-Z]{1,5}\s+[A-Z]|"
    r"[A-Za-z0-9_./-]+\.[a-zA-Z]{1,5}\s*$"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

_SEVERITY_EMOJI = {
    "critical": "🔴",
    "warning": "🟡",
    "suggestion": "🟢",
}

_PROTOCOL_ROOTS = {
    "comment": ("<COMMENT>", "</COMMENT>"),
    "overview": ("<OVERVIEW>", "</OVERVIEW>"),
    "unit_review": ("<UNIT_REVIEW>", "</UNIT_REVIEW>"),
}
_URL_LINE_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_ENGLISH_HEAVY_RE = re.compile(r"[A-Za-z]{4,}")
_HANGUL_RE = re.compile(r"[가-힣]")


def _escape_table_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def sanitize_gitlab_markdown(text: str) -> str:
    """GitLab 렌더링이 깨지지 않도록 최소 정규화한다."""
    output = str(text or "").replace("\r\n", "\n").strip()
    output = re.sub(r"\n{3,}", "\n\n", output)
    if output.count("```") % 2 == 1:
        output += "\n```"
    return output


def _contains_hangul(text: str) -> bool:
    return bool(_HANGUL_RE.search(str(text or "")))


def _is_english_heavy(text: str) -> bool:
    if not text:
        return False
    return bool(_ENGLISH_HEAVY_RE.search(text)) and not _contains_hangul(text)


def _normalize_freeform_paragraphs(raw: str) -> list[str]:
    text = sanitize_gitlab_markdown(raw)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    cleaned: list[str] = []
    seen: set[str] = set()

    has_korean = any(_contains_hangul(part) for part in paragraphs)
    for part in paragraphs:
        part = re.sub(r"\n{2,}", "\n", part).strip()
        if not part:
            continue
        if _URL_LINE_RE.match(part):
            continue
        if "Would you like me to review" in part:
            continue
        if "I've reviewed the provided files and the diff." in part and has_korean:
            continue
        if _is_english_heavy(part) and has_korean:
            continue
        dedupe_key = re.sub(r"\s+", " ", part)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cleaned.append(part)
    return cleaned


def _extract_tag_block(text: str, tag: str) -> Optional[str]:
    match = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def _extract_tag_blocks(text: str, tag: str) -> list[str]:
    return [match.strip() for match in re.findall(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.DOTALL)]


def _parse_protocol_output(text: str, schema: str) -> Optional[dict]:
    root = _PROTOCOL_ROOTS.get(schema)
    if not root:
        return None

    start_tag, end_tag = root
    start = text.find(start_tag)
    end = text.rfind(end_tag)
    if start == -1 or end == -1 or end <= start:
        return None
    body = text[start + len(start_tag):end].strip()

    if schema == "comment":
        result = {
            "conclusion": _extract_tag_block(body, "CONCLUSION") or "",
            "analysis": _extract_tag_block(body, "ANALYSIS") or "",
            "suggestions": [],
        }
        for suggestion_body in _extract_tag_blocks(body, "SUGGESTION"):
            result["suggestions"].append(
                {
                    "severity": (_extract_tag_block(suggestion_body, "SEVERITY") or "suggestion").strip(),
                    "description": _extract_tag_block(suggestion_body, "DESCRIPTION") or "",
                    "file": _extract_tag_block(suggestion_body, "FILE") or "",
                    "language": _extract_tag_block(suggestion_body, "LANGUAGE") or "",
                    "before": _extract_tag_block(suggestion_body, "BEFORE") or "",
                    "after": _extract_tag_block(suggestion_body, "AFTER") or "",
                }
            )
        return result

    if schema == "overview":
        result = {
            "title": _extract_tag_block(body, "TITLE") or "",
            "summary": _extract_tag_block(body, "SUMMARY") or "",
            "file_changes": [],
            "review_points": [],
        }
        for file_change_body in _extract_tag_blocks(body, "FILE_CHANGE"):
            result["file_changes"].append(
                {
                    "file": _extract_tag_block(file_change_body, "FILE") or "",
                    "change": _extract_tag_block(file_change_body, "CHANGE") or "",
                }
            )
        for review_point_body in _extract_tag_blocks(body, "REVIEW_POINT"):
            result["review_points"].append(
                {
                    "severity": (_extract_tag_block(review_point_body, "SEVERITY") or "suggestion").strip(),
                    "description": _extract_tag_block(review_point_body, "DESCRIPTION") or "",
                    "file": _extract_tag_block(review_point_body, "FILE") or "",
                }
            )
        return result

    if schema == "unit_review":
        result = {"findings": []}
        for finding_body in _extract_tag_blocks(body, "FINDING"):
            result["findings"].append(
                {
                    "severity": (_extract_tag_block(finding_body, "SEVERITY") or "suggestion").strip(),
                    "title": _extract_tag_block(finding_body, "TITLE") or "",
                    "description": _extract_tag_block(finding_body, "DESCRIPTION") or "",
                    "file": _extract_tag_block(finding_body, "FILE") or "",
                    "lines": _extract_tag_block(finding_body, "LINES") or "",
                    "confidence": _extract_tag_block(finding_body, "CONFIDENCE") or "",
                }
            )
        return result

    return None


def _severity_label(severity: str) -> str:
    key = severity.lower() if severity else ""
    emoji = _SEVERITY_EMOJI.get(key, "🟢")
    label = severity.capitalize() if severity else "Suggestion"
    return f"{emoji} **{label}**"


def _extract_json_block(text: str) -> Optional[str]:
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()

    start = -1
    opening = ""
    for idx, ch in enumerate(text):
        if ch in "{[":
            start = idx
            opening = ch
            break
    if start == -1:
        return None

    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start:idx + 1].strip()
    return None


def _coerce_schema(data: dict, schema: str) -> Optional[dict]:
    if not isinstance(data, dict):
        return None

    if schema == "overview":
        result = {
            "title": str(data.get("title", "")).strip(),
            "summary": str(data.get("summary", "")).strip(),
            "file_changes": [],
            "review_points": [],
        }
        for item in data.get("file_changes", []) or []:
            if isinstance(item, dict):
                result["file_changes"].append(
                    {
                        "file": str(item.get("file", "")).strip(),
                        "change": str(item.get("change", "")).strip(),
                    }
                )
        for item in data.get("review_points", []) or []:
            if isinstance(item, dict):
                result["review_points"].append(
                    {
                        "severity": str(item.get("severity", "suggestion")).strip() or "suggestion",
                        "description": str(item.get("description", "")).strip(),
                        "file": str(item.get("file", "")).strip(),
                    }
                )
        return result

    if schema == "comment":
        result = {
            "conclusion": str(data.get("conclusion", "")).strip(),
            "analysis": str(data.get("analysis", "")).strip(),
            "suggestions": [],
        }
        for item in data.get("suggestions", []) or []:
            if isinstance(item, dict):
                result["suggestions"].append(
                    {
                        "severity": str(item.get("severity", "suggestion")).strip() or "suggestion",
                        "description": str(item.get("description", "")).strip(),
                        "file": str(item.get("file", "")).strip(),
                        "language": str(item.get("language", "")).strip(),
                        "before": str(item.get("before", "")).rstrip(),
                        "after": str(item.get("after", "")).rstrip(),
                    }
                )
        return result

    if schema == "unit_review":
        result = {"findings": []}
        for item in data.get("findings", []) or []:
            if isinstance(item, dict):
                result["findings"].append(
                    {
                        "severity": str(item.get("severity", "suggestion")).strip() or "suggestion",
                        "title": str(item.get("title", "")).strip(),
                        "description": str(item.get("description", "")).strip(),
                        "file": str(item.get("file", "")).strip(),
                        "lines": str(item.get("lines", "")).strip(),
                        "confidence": str(item.get("confidence", "")).strip(),
                    }
                )
        return result

    return data


def parse_structured_output(text: str, schema: str) -> Optional[dict]:
    """프로토콜 우선, 이후 JSON/YAML 순으로 파싱한다."""
    protocol_data = _parse_protocol_output(text, schema)
    if protocol_data is not None:
        return _coerce_schema(protocol_data, schema)

    json_block = _extract_json_block(text)
    if json_block:
        try:
            data = json.loads(json_block)
            coerced = _coerce_schema(data, schema)
            if coerced is not None:
                return coerced
        except json.JSONDecodeError:
            pass

    if "```yaml" not in text and "```YAML" not in text and "title:" not in text and "conclusion:" not in text and "findings:" not in text:
        return None

    yaml_data = parse_yaml_safe(text, schema=schema)
    if yaml_data is None:
        return None
    return _coerce_schema(yaml_data, schema)


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


def _repair_block_scalar_code(text: str) -> str:
    """before/after 블록 스칼라의 코드 내용을 double-quoted 스칼라로 변환한다.

    코드에 포함된 YAML 특수문자(:, {, [, #, ---)로 인한 파싱 실패를 방지한다.
    before/after 키에만 적용하고, summary 등 일반 텍스트 블록에는 적용하지 않는다.
    """
    lines = text.splitlines()
    result = []
    in_code_block = False
    code_key_indent = 0
    code_lines: list[str] = []
    code_key_prefix = ""

    def _flush_code_block():
        """수집된 코드 줄을 double-quoted 스칼라로 변환하여 result에 추가."""
        if not code_lines:
            result.append(code_key_prefix + '""')
            return
        merged = "\\n".join(code_lines)
        escaped = merged.replace("\\", "\\\\").replace('"', '\\"')
        # 이미 \\n으로 변환한 줄바꿈은 보존
        escaped = escaped.replace("\\\\n", "\\n")
        result.append(f'{code_key_prefix}"{escaped}"')

    for line in lines:
        stripped = line.strip()

        if in_code_block:
            if not stripped:
                code_lines.append("")
                continue

            current_indent = len(line) - len(line.lstrip())
            key_match = _YAML_KEY_LINE_RE.match(line)

            if key_match and len(key_match.group(1)) <= code_key_indent:
                # 새 키가 같거나 상위 레벨 → 블록 종료
                _flush_code_block()
                in_code_block = False
                code_lines = []
                # 현재 줄을 다시 처리
            elif current_indent > code_key_indent:
                code_lines.append(stripped)
                continue
            else:
                # 들여쓰기가 부족한 비-키 줄 → 블록 종료
                _flush_code_block()
                in_code_block = False
                code_lines = []

        # before:/after: 블록 스칼라 감지
        m = _BLOCK_SCALAR_LINE_RE.match(line)
        if m:
            kv = _YAML_KEY_VALUE_RE.match(line)
            if kv and kv.group(2) in ("before", "after"):
                code_key_indent = len(kv.group(1))
                code_key_prefix = kv.group(1) + kv.group(2) + ": "
                in_code_block = True
                code_lines = []
                continue

        result.append(line)

    if in_code_block:
        _flush_code_block()

    return "\n".join(result)


def _quote_colon_in_plain_scalars(text: str) -> str:
    """plain scalar 값에 ': ' 가 포함된 경우 YAML 파싱 오류를 방지하기 위해 큰따옴표로 감싼다.

    YAML block context에서 plain scalar 안의 ': ' (콜론+공백)는 새 key-value 시작으로
    해석되어 파싱이 깨진다. 예: '형식(예: 헤더, 쿠키 등)' → '"형식(예: 헤더, 쿠키 등)"'
    block scalar(|, >), 이미 따옴표로 감싼 값, flow indicator({, [)는 건드리지 않는다.

    시퀀스 아이템 내 key-value 패턴(`- severity: ...`)과
    한국어 뒤 콜론(공백 없음) 패턴도 처리한다.
    """
    lines = text.splitlines()
    result = []
    for line in lines:
        # 일반 key: value 패턴
        m = _PLAIN_SCALAR_COLON_RE.match(line)
        if m:
            key_part = m.group(1)
            value = m.group(2).strip()
            if _needs_quoting(value):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                result.append(f'{key_part}"{escaped}"')
                continue
        # 시퀀스 아이템 내 key: value 패턴 (- severity: ...)
        sm = _SEQ_ITEM_KV_RE.match(line)
        if sm:
            seq_prefix = sm.group(1)
            kv_key = sm.group(2)
            value = sm.group(3).strip()
            if _needs_quoting(value):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                result.append(f'{seq_prefix}{kv_key}"{escaped}"')
                continue
        result.append(line)
    return "\n".join(result)


def _needs_quoting(value: str) -> bool:
    """값에 따옴표가 필요한지 판단한다."""
    if not value or value[0] in ("|", ">", '"', "'", "{", "["):
        return False
    # 콜론+공백 또는 끝 콜론
    if ": " in value or value.endswith(":"):
        return True
    # 한국어 뒤 콜론 (공백 없음): 예: [가-힣]:
    if re.search(r"[\uac00-\ud7a3]:", value):
        return True
    return False


_YAML_OPEN_FENCE_RE = re.compile(r"```(?:yaml|YAML)\s*\n(.*)", re.DOTALL)


def _extract_top_level_section(text: str, key: str, next_keys: list[str]) -> Optional[str]:
    """최상위 key 구간을 추출한다."""
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(.*)$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return None

    start = match.start()
    end = len(text)
    for next_key in next_keys:
        next_pattern = re.compile(rf"^{re.escape(next_key)}\s*:", re.MULTILINE)
        next_match = next_pattern.search(text, match.end())
        if next_match:
            end = min(end, next_match.start())
    return text[start:end].strip()


def _extract_scalar_value(text: str, key: str, next_keys: list[str]) -> Optional[str]:
    """top-level scalar 값을 최대한 보존해서 추출한다."""
    section = _extract_top_level_section(text, key, next_keys)
    if not section:
        return None

    lines = section.splitlines()
    if not lines:
        return None

    first = lines[0]
    _, _, remainder = first.partition(":")
    value = remainder.strip()

    if value in ("|", ">") or value.startswith("|") or value.startswith(">"):
        body = []
        for line in lines[1:]:
            body.append(line.strip())
        return "\n".join(item for item in body).strip()

    if value:
        extra_lines = []
        for line in lines[1:]:
            stripped = line.strip()
            if stripped:
                extra_lines.append(stripped)
        if extra_lines:
            return " ".join([value, *extra_lines]).strip().strip('"').strip("'")
        return value.strip().strip('"').strip("'")

    return None


def extract_yaml_block(text: str) -> Optional[str]:
    """LLM 출력에서 YAML 블록을 추출한다.

    1순위: ```yaml ... ``` 펜스 탐색
    2순위: 닫힌 펜스 없이 ```yaml 이후 끝까지 추출
    3순위: 'title:' / 'conclusion:' / 'findings:' 로 시작하는 줄부터 끝까지 추출
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
        if stripped.startswith("title:") or stripped.startswith("conclusion:") or stripped.startswith("findings:"):
            return "\n".join(lines[i:]).strip()

    return None


def _extract_fields_by_regex(text: str, schema: str) -> Optional[dict]:
    """YAML 파싱이 완전히 실패했을 때 정규식으로 개별 필드를 추출하여 partial dict를 반환한다."""
    result: dict = {}

    if schema == "overview":
        # title
        m = re.search(r"^title\s*:\s*(.+)", text, re.MULTILINE)
        if m:
            result["title"] = m.group(1).strip().strip('"').strip("'")
        # summary (block scalar 또는 plain)
        m = re.search(r"^summary\s*:\s*\|?\s*\n((?:[ \t]+.+\n?)+)", text, re.MULTILINE)
        if m:
            result["summary"] = "\n".join(l.strip() for l in m.group(1).splitlines()).strip()
        elif not result.get("summary"):
            m2 = re.search(r"^summary\s*:\s*(.+)", text, re.MULTILINE)
            if m2:
                result["summary"] = m2.group(1).strip().strip('"').strip("'")
        # file_changes
        fc_items = re.findall(r"-\s*file\s*:\s*(.+)\n\s*change\s*:\s*(.+)", text)
        if fc_items:
            result["file_changes"] = [{"file": f.strip(), "change": c.strip()} for f, c in fc_items]
        # review_points
        rp_items = re.findall(r"-\s*severity\s*:\s*(.+)\n\s*description\s*:\s*(.+?)(?:\n\s*file\s*:\s*(.+))?(?:\n|$)", text)
        if rp_items:
            result["review_points"] = []
            for sev, desc, fref in rp_items:
                rp: dict = {"severity": sev.strip(), "description": desc.strip()}
                if fref and fref.strip():
                    rp["file"] = fref.strip()
                result["review_points"].append(rp)

    elif schema == "comment":
        conclusion = _extract_scalar_value(text, "conclusion", ["analysis", "suggestions"])
        if conclusion:
            result["conclusion"] = conclusion

        analysis = _extract_scalar_value(text, "analysis", ["suggestions"])
        if analysis:
            result["analysis"] = analysis

        suggestions_section = _extract_top_level_section(text, "suggestions", [])
        if suggestions_section:
            try:
                parsed = yaml.safe_load(suggestions_section)
                if isinstance(parsed, dict) and isinstance(parsed.get("suggestions"), list):
                    result["suggestions"] = parsed["suggestions"]
            except yaml.YAMLError:
                sg_items = re.findall(
                    r"-\s*severity\s*:\s*(.+)\n"
                    r"\s*description\s*:\s*(.+(?:\n(?!\s*(?:file|language|before|after|-\s*severity)\s*:).+)*)"
                    r"(?:\n\s*file\s*:\s*(.+))?"
                    r"(?:\n|$)",
                    text,
                )
                if sg_items:
                    result["suggestions"] = []
                    for sev, desc, fref in sg_items:
                        sg: dict = {"severity": sev.strip(), "description": " ".join(line.strip() for line in desc.splitlines()).strip()}
                        if fref and fref.strip():
                            sg["file"] = fref.strip()
                        result["suggestions"].append(sg)

    elif schema == "unit_review":
        findings = re.findall(
            r"-\s*severity\s*:\s*(.+)\n"
            r"(?:\s*title\s*:\s*(.+)\n)?"
            r"\s*description\s*:\s*(.+?)"
            r"(?:\n\s*file\s*:\s*(.+))?"
            r"(?:\n\s*lines\s*:\s*(.+))?"
            r"(?:\n\s*confidence\s*:\s*(.+))?"
            r"(?:\n|$)",
            text,
        )
        if findings:
            result["findings"] = []
            for sev, title, desc, file_ref, lines_ref, confidence in findings:
                finding: dict = {
                    "severity": sev.strip(),
                    "description": desc.strip(),
                }
                if title and title.strip():
                    finding["title"] = title.strip().strip('"').strip("'")
                if file_ref and file_ref.strip():
                    finding["file"] = file_ref.strip()
                if lines_ref and lines_ref.strip():
                    finding["lines"] = lines_ref.strip().strip('"').strip("'")
                if confidence and confidence.strip():
                    finding["confidence"] = confidence.strip().strip('"').strip("'")
                result["findings"].append(finding)
        elif re.search(r"^findings\s*:\s*\[\s*\]\s*$", text, re.MULTILINE):
            result["findings"] = []

    if not result:
        return None
    logger.info(f"ℹ️ _extract_fields_by_regex: {schema} 스키마에서 {list(result.keys())} 필드 추출 성공")
    return result


def render_raw_fallback(raw: str) -> str:
    """모든 파싱이 실패했을 때 최소한 깨끗한 마크다운을 생성한다."""
    lines = raw.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # aider 메타 라인 제거
        if _AIDER_META_LINE.match(stripped):
            continue
        cleaned.append(line)

    text = "\n".join(cleaned)

    # 닫히지 않은 코드 펜스 자동 닫기
    fence_count = text.count("```")
    if fence_count % 2 == 1:
        text += "\n```"

    # 과도한 빈 줄 정리 (3줄 이상 → 2줄)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    header = "> ⚠️ AI 응답을 구조화하지 못했습니다.\n"
    return header + "\n" + text if text else header


def render_comment_from_freeform(raw: str) -> str:
    """자유형 텍스트를 GitLab 댓글용 마크다운으로 안전하게 렌더링한다."""
    paragraphs = _normalize_freeform_paragraphs(raw)
    if not paragraphs:
        return (
            "## 💡 결론\n"
            "응답을 안정적으로 구조화하지 못했습니다.\n\n"
            "## 🔍 상세 분석\n"
            "이번 요청은 자유형 응답만 생성되어 한국어 구조화 결과를 만들지 못했습니다. "
            "로그에서 `llm-client 구조화 실패` 여부를 확인해 주세요."
        )

    korean_paragraphs = [part for part in paragraphs if _contains_hangul(part)]
    if korean_paragraphs:
        paragraphs = korean_paragraphs
    else:
        return (
            "## 💡 결론\n"
            "영문 자유형 응답만 생성되어 구조화된 한국어 답변을 만들지 못했습니다.\n\n"
            "## 🔍 상세 분석\n"
            "현재 응답은 GitLab에 그대로 게시하지 않고 생략했습니다. "
            "DEBUG 로그에서 `aider raw stdout`과 `llm-client 응답 전문`을 확인해 주세요."
        )

    conclusion = paragraphs[0]
    remainder = "\n\n".join(paragraphs[1:]).strip()

    parts = [f"## 💡 결론\n{conclusion}"]
    if remainder:
        parts.append(f"## 🔍 상세 분석\n{remainder}")
    return sanitize_gitlab_markdown("\n\n".join(parts))


def render_overview_from_freeform(raw: str, original_title: str) -> tuple[str, str]:
    """자유형 overview 텍스트를 MR description 용 마크다운으로 변환한다."""
    text = sanitize_gitlab_markdown(raw)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        return original_title, (
            "> ⚠️ AI overview를 구조화하지 못했습니다.\n\n"
            "## 📋 변경 개요\n이번 변경에 대한 요약 생성에 실패했습니다."
        )

    summary = paragraphs[0]
    remainder = "\n\n".join(paragraphs[1:]).strip()
    sections = [
        "> 🤖 이 설명은 Aider AI가 자동 생성했습니다.\n",
        f"## 📋 변경 개요\n{summary}",
    ]
    if remainder:
        sections.append(f"## 🔍 추가 분석\n{remainder}")
    sections.append("---\n<sub>🤖 Aider AI Code Review Bot 자동 생성</sub>")
    return original_title, sanitize_gitlab_markdown("\n\n".join(sections))


def parse_yaml_safe(text: str, schema: Optional[str] = None) -> Optional[dict]:
    """LLM 출력 텍스트에서 YAML을 파싱하여 dict를 반환한다. 실패 시 None.

    Args:
        text: LLM 원본 출력 텍스트
        schema: regex 폴백 시 사용할 스키마 ("overview" | "comment" | "unit_review", optional)
    """
    logger.debug(f"디버깅 텍스트: {text}")

    block = extract_yaml_block(text)
    if block is None:
        logger.warning("⚠️ parse_yaml_safe: YAML 블록을 찾지 못함")
        # regex 폴백 시도
        if schema:
            return _extract_fields_by_regex(text, schema)
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

    # 3차 시도: before/after 코드 블록을 quoted 스칼라로 변환 후 재파싱
    code_repaired = _repair_block_scalar_code(repaired)
    try:
        data = yaml.safe_load(code_repaired)
        if isinstance(data, dict):
            logger.info("ℹ️ parse_yaml_safe: 코드 블록 quoted 변환 후 파싱 성공")
            return data
    except yaml.YAMLError:
        pass

    # 4차 시도: plain scalar 내 ': ' 큰따옴표 이스케이프 후 재파싱
    quoted = _quote_colon_in_plain_scalars(code_repaired)
    try:
        data = yaml.safe_load(quoted)
        if isinstance(data, dict):
            logger.info("ℹ️ parse_yaml_safe: colon 이스케이프 후 파싱 성공")
            return data
    except yaml.YAMLError:
        pass

    # 5차 시도: 빈 줄 기준으로 뒤에서부터 잘라내며 재파싱
    segments = re.split(r'\n\s*\n', quoted)
    for n in range(len(segments) - 1, 0, -1):
        truncated = '\n\n'.join(segments[:n])
        try:
            data = yaml.safe_load(truncated)
            if isinstance(data, dict) and ("title" in data or "conclusion" in data or "findings" in data):
                logger.info(f"ℹ️ parse_yaml_safe: 후미 텍스트 제거 후 파싱 성공 (segments={n}/{len(segments)})")
                return data
        except yaml.YAMLError:
            pass

    # 6차 시도: regex 기반 필드 추출
    if schema:
        regex_result = _extract_fields_by_regex(quoted, schema)
        if regex_result:
            return regex_result

    logger.warning("⚠️ parse_yaml_safe: YAML 파싱 최종 실패\n--- block ---\n%s\n---", block)
    return None


def render_overview_markdown(data: dict) -> tuple[str, str]:
    """YAML dict를 overview (title, description) 마크다운으로 렌더링한다."""
    title = sanitize_gitlab_markdown(str(data.get("title", "")).strip()).replace("\n", " ")

    sections = ["> 🤖 이 설명은 Aider AI가 자동 생성했습니다.\n"]

    summary = data.get("summary")
    if summary:
        sections.append(f"## 📋 변경 개요\n{sanitize_gitlab_markdown(str(summary).strip())}")

    file_changes = data.get("file_changes")
    if file_changes and isinstance(file_changes, list):
        rows = []
        for idx, fc in enumerate(file_changes, 1):
            if not isinstance(fc, dict):
                continue
            fname = fc.get("file", "")
            change = fc.get("change", "")
            rows.append(f"| {idx} | `{_escape_table_cell(fname)}` | {_escape_table_cell(change)} |")
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
            bullet = f"- {label}: {sanitize_gitlab_markdown(desc)}"
            if file_ref:
                bullet += f" (`{sanitize_gitlab_markdown(file_ref).replace(chr(10), ' ')}`)"
            bullets.append(bullet)
        if bullets:
            sections.append("## ⚠️ 리뷰 포인트\n" + "\n".join(bullets))

    sections.append("---\n<sub>🤖 Aider AI Code Review Bot 자동 생성</sub>")

    description = sanitize_gitlab_markdown("\n\n".join(sections))
    return title, description


def render_comment_markdown(data: dict) -> str:
    """YAML dict를 comment 마크다운으로 렌더링한다."""
    parts = []

    conclusion = data.get("conclusion")
    if conclusion:
        parts.append(f"## 💡 결론\n{sanitize_gitlab_markdown(str(conclusion).strip())}")

    analysis = data.get("analysis")
    if analysis:
        parts.append(f"## 🔍 상세 분석\n{sanitize_gitlab_markdown(str(analysis).strip())}")

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
            line = f"\n{label} **—** {sanitize_gitlab_markdown(desc)}"
            if file_ref:
                line += f"\n`{sanitize_gitlab_markdown(file_ref).replace(chr(10), ' ')}`"
            suggestion_parts.append(line)

            before_str = str(before).strip() if before else ""
            after_str = str(after).strip() if after else ""
            if before_str and after_str and before_str != after_str:
                lang_fence = str(sg.get("language", "")).strip()
                suggestion_parts.append(f"\n**Before**\n```{lang_fence}\n{sanitize_gitlab_markdown(before_str)}\n```")
                suggestion_parts.append(f"**After**\n```{lang_fence}\n{sanitize_gitlab_markdown(after_str)}\n```")

        parts.append("\n".join(suggestion_parts))

    return sanitize_gitlab_markdown("\n\n".join(parts))
