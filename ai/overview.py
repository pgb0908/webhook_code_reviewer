# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import logging
from typing import Optional

from config import settings
from git.diff import DiffResult, split_diff_into_chunks, extract_changed_files
from ai.shared.subprocess import run_aider_subprocess
from ai.shared.output import parse_yaml_safe, render_overview_markdown

logger = logging.getLogger(__name__)


def _build_overview_prompt(diff_result: DiffResult, original_title: str) -> str:
    return f"""[작업 지시]
우리 팀 수석 SRE이자 C++ 백엔드 전문가로서, 아래 Merge Request diff를 분석하여
정확히 지정된 YAML 스키마로만 출력하라.
YAML 펜스(```yaml ... ```) 외 인사말·부연 설명·지시 반복은 절대 출력하지 마라.

⚠️ 중요: 모든 텍스트 값을 반드시 한국어(한글)로만 작성하라. 영어 사용 절대 금지.

원래 MR 제목: {original_title}

[Diff]
```diff
{diff_result.content}
```

[출력 형식 — ```yaml 펜스 안에 아래 스키마를 채워 출력할 것]

```yaml
title: <동사로 시작, 40자 이내 — 반드시 한국어로>
summary: |
  <이번 변경의 목적과 접근 방식. 왜 필요했는지 + 무엇을 어떻게 바꿨는지를 3~5문장으로 서술. 반드시 한국어로>
file_changes:
  - file: <파일명>
    change: <변경 내용 1문장 — 반드시 한국어로>
review_points:
  - severity: critical
    description: <문제 설명 — 반드시 한국어로>
    file: <해당 파일명, 없으면 생략>
```

모든 텍스트 값(title, summary, change, description)은 반드시 한국어로 작성할 것. 영어 사용 금지.
"""


def _build_chunk_analysis_prompt(chunk: str, chunk_index: int, total_chunks: int) -> str:
    return f"""[작업 지시]
아래는 Merge Request diff의 일부({chunk_index}/{total_chunks} 청크)다.
변경된 각 파일에 대해 아래 형식으로만 출력하라. 인사말·부연 없이. 응답은 한글로.

형식:
FILE: <파일명>
CHANGES: <변경 내용 1~2문장>
CONCERNS: <심각도 레이블 포함 문제 목록. 없으면 "없음">
  심각도: 🔴 Critical | 🟡 Warning | 🟢 Suggestion
  예시: 🔴 Critical — 메모리 해제 누락

[Diff 청크]
```diff
{chunk}
```
"""


def _build_aggregate_prompt(partial_analyses: list[str], original_title: str) -> str:
    combined = "\n\n---\n\n".join(
        f"[청크 {i + 1}]\n{a}" for i, a in enumerate(partial_analyses)
    )
    return f"""[작업 지시]
우리 팀 수석 SRE이자 C++ 백엔드 전문가로서, 아래 청크별 분석을 종합하여
정확히 지정된 YAML 스키마로만 출력하라.
YAML 펜스(```yaml ... ```) 외 인사말·부연 설명·지시 반복은 절대 출력하지 마라.

⚠️ 중요: 모든 텍스트 값을 반드시 한국어(한글)로만 작성하라. 영어 사용 절대 금지.

원래 MR 제목: {original_title}

[청크별 분석]
{combined}

[출력 형식 — ```yaml 펜스 안에 아래 스키마를 채워 출력할 것]

```yaml
title: <동사로 시작, 40자 이내 — 반드시 한국어로>
summary: |
  <목적과 접근 방식. 3~5문장 — 반드시 한국어로>
file_changes:
  - file: <파일명>
    change: <변경 내용 1문장 — 반드시 한국어로>
review_points:
  - severity: critical
    description: <문제 설명 — 반드시 한국어로>
    file: <해당 파일명, 없으면 생략>
```

모든 텍스트 값(title, summary, change, description)은 반드시 한국어로 작성할 것. 영어 사용 금지.
"""


def parse_overview_output(raw: str) -> tuple[str, str]:
    """aider 출력에서 YAML을 파싱하여 (title, description)을 반환한다."""
    data = parse_yaml_safe(raw)
    if data and "title" in data:
        return render_overview_markdown(data)

    # Fallback: 기존 TITLE: 파싱 로직 (하위 호환)
    logger.warning("⚠️ parse_overview_output: YAML 파싱 실패, 레거시 TITLE: 파싱 시도")
    lines = raw.strip().splitlines()
    title = ""
    title_line_idx = -1

    for i, line in enumerate(lines):
        if line.strip().startswith("TITLE:"):
            title = line.strip()[len("TITLE:"):].strip()
            title_line_idx = i
            break

    if not title:
        logger.warning("⚠️ parse_overview_output: TITLE 포맷 감지 실패, fallback 사용")
        return "", raw

    desc_start = title_line_idx + 1
    for i in range(title_line_idx + 1, min(title_line_idx + 4, len(lines))):
        if lines[i].strip() == "---":
            desc_start = i + 1
            break

    description = "\n".join(lines[desc_start:]).strip()
    return title, description


def run_aider_overview(
        mr_iid: str,
        workspace_path: str,
        diff_result: DiffResult,
        original_title: str,
) -> Optional[tuple[str, str]]:
    """Aider CLI를 실행하여 (title, description) 튜플을 반환한다. 실패 시 None."""
    chunks = split_diff_into_chunks(diff_result.content, settings.diff_max_chars)
    logger.info(f"🧠 [MR #{mr_iid}] diff 청크 수: {len(chunks)}")

    if len(chunks) == 1:
        # 단일 청크: 기존 플로우
        prompt = _build_overview_prompt(diff_result, original_title)
        file_paths = extract_changed_files(diff_result.content)
        raw = run_aider_subprocess(mr_iid, workspace_path, prompt, file_paths)
        if raw is None:
            return None
        title, description = parse_overview_output(raw)
        return title or original_title, description

    # Map: 청크별 분석
    partial_analyses = []
    for idx, chunk in enumerate(chunks, 1):
        logger.info(f"🔍 [MR #{mr_iid}] 청크 {idx}/{len(chunks)} 분석 중...")
        prompt = _build_chunk_analysis_prompt(chunk, idx, len(chunks))
        chunk_files = extract_changed_files(chunk)
        result = run_aider_subprocess(mr_iid, workspace_path, prompt, chunk_files)
        if result:
            partial_analyses.append(result)
        else:
            logger.warning(f"⚠️ [MR #{mr_iid}] 청크 {idx} 분석 실패, 건너뜀")

    if not partial_analyses:
        logger.error(f"❌ [MR #{mr_iid}] 모든 청크 분석 실패")
        return None

    # Reduce: 취합
    logger.info(f"📝 [MR #{mr_iid}] {len(partial_analyses)}개 청크 분석 취합 중...")
    aggregate_prompt = _build_aggregate_prompt(partial_analyses, original_title)
    all_files = extract_changed_files(diff_result.content)
    raw = run_aider_subprocess(mr_iid, workspace_path, aggregate_prompt, all_files)
    if raw is None:
        return None
    title, description = parse_overview_output(raw)
    return title or original_title, description
