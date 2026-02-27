import logging
from typing import Optional

from git_service import DiffResult
from service.common.aider_subprocess import _run_aider_subprocess

logger = logging.getLogger(__name__)


def _build_overview_prompt(diff_result: DiffResult, original_title: str) -> str:
    truncation_warning = ""
    if diff_result.truncated:
        truncation_warning = (
            f"\n⚠️ **주의**: Diff가 너무 커서 앞부분 {len(diff_result.content)}자만 포함되었습니다 "
            f"(전체 {diff_result.original_length}자).\n"
        )

    return f"""[작업 지시]
우리 팀 수석 SRE이자 C++ 백엔드 전문가로서, 아래 Merge Request diff를 분석하여
정확히 지정된 형식으로만 MR 설명 문서를 작성하라.
형식 외 인사말·부연 설명·지시 반복은 절대 출력하지 마라. 응답은 한글로.

원래 MR 제목: {original_title}
{truncation_warning}
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
    logger.info(f"🧠 [MR #{mr_iid}] Aider MR overview 보고서 생성 중...")
    prompt = _build_overview_prompt(diff_result, original_title)
    raw = _run_aider_subprocess(settings, mr_iid, workspace_path, prompt)
    if raw is None:
        return None
    return parse_overview_output(raw)
