import logging
from typing import Optional

from git_service import DiffResult, split_diff_into_chunks
from service.common.aider_subprocess import _run_aider_subprocess

logger = logging.getLogger(__name__)


def _build_overview_prompt(diff_result: DiffResult, original_title: str) -> str:
    return f"""[작업 지시]
우리 팀 수석 SRE이자 C++ 백엔드 전문가로서, 아래 Merge Request diff를 분석하여
정확히 지정된 형식으로만 MR 설명 문서를 작성하라.
형식 외 인사말·부연 설명·지시 반복은 절대 출력하지 마라. 응답은 한글로.

원래 MR 제목: {original_title}

[Diff]
```diff
{diff_result.content}
```

[출력 형식 — < > 부분을 실제 내용으로 채울 것]

TITLE: <동사로 시작, 40자 이내>
---
> 🤖 이 설명은 Aider AI가 자동 생성했습니다.

## 📋 변경 개요
<이번 변경의 목적과 접근 방식. 왜 필요했는지 + 무엇을 어떻게 바꿨는지를 3~5문장으로 서술>

## 🔍 주요 변경 사항
<변경된 파일·컴포넌트마다 한 항목씩. 형식: `번호. **파일명** — 변경 내용 1~2문장`>

## ⚠️ 리뷰 포인트
<잠재 버그·성능·메모리 우려사항·개선 제안을 불릿으로. 없으면 "특이사항 없음">

---
*Aider AI Code Review Bot 자동 생성*
"""


def _build_chunk_analysis_prompt(chunk: str, chunk_index: int, total_chunks: int) -> str:
    return f"""[작업 지시]
아래는 Merge Request diff의 일부({chunk_index}/{total_chunks} 청크)다.
변경된 각 파일에 대해 아래 형식으로만 출력하라. 인사말·부연 없이.

형식:
FILE: <파일명>
CHANGES: <변경 내용 1~2문장>
CONCERNS: <잠재 문제나 리뷰 포인트. 없으면 "없음">

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
정확히 지정된 형식으로만 MR 설명 문서를 작성하라.
형식 외 인사말·부연 설명·지시 반복은 절대 출력하지 마라. 응답은 한글로.

원래 MR 제목: {original_title}

[청크별 분석]
{combined}

[출력 형식]
TITLE: <동사로 시작, 40자 이내>
---
> 🤖 이 설명은 Aider AI가 자동 생성했습니다.

## 📋 변경 개요
<목적과 접근 방식. 3~5문장>

## 🔍 주요 변경 사항
<변경된 파일·컴포넌트마다 한 항목씩. 형식: `번호. **파일명** — 변경 내용 1~2문장`>

## ⚠️ 리뷰 포인트
<잠재 버그·성능·메모리 우려사항·개선 제안을 불릿으로. 없으면 "특이사항 없음">

---
*Aider AI Code Review Bot 자동 생성*
"""


def parse_overview_output(raw: str) -> tuple[str, str]:
    """aider 출력에서 TITLE과 description을 파싱한다."""
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

    # TITLE 줄 직후 최대 3줄 안에서 --- 구분자 탐색
    # --- 가 있으면 그 다음 줄부터, 없으면 TITLE 다음 줄부터 description
    desc_start = title_line_idx + 1
    for i in range(title_line_idx + 1, min(title_line_idx + 4, len(lines))):
        if lines[i].strip() == "---":
            desc_start = i + 1
            break

    description = "\n".join(lines[desc_start:]).strip()
    return title, description


def run_aider_overview(
        settings,
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
        raw = _run_aider_subprocess(settings, mr_iid, workspace_path, prompt)
        if raw is None:
            return None
        return parse_overview_output(raw)

    # Map: 청크별 분석
    partial_analyses = []
    for idx, chunk in enumerate(chunks, 1):
        logger.info(f"🔍 [MR #{mr_iid}] 청크 {idx}/{len(chunks)} 분석 중...")
        prompt = _build_chunk_analysis_prompt(chunk, idx, len(chunks))
        result = _run_aider_subprocess(settings, mr_iid, workspace_path, prompt)
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
    raw = _run_aider_subprocess(settings, mr_iid, workspace_path, aggregate_prompt)
    if raw is None:
        return None
    return parse_overview_output(raw)
