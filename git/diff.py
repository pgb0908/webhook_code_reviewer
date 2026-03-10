# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import os
import re
import logging
import subprocess
from collections import Counter
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Optional

from config import settings

_EXT_TO_LANG: dict[str, str] = {
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c": "c",
    ".h": "cpp", ".hpp": "cpp",
    ".java": "java",
    ".py": "python",
    ".go": "go",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".rs": "rust",
    ".kt": "kotlin", ".kts": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "bash",
}

logger = logging.getLogger(__name__)

_DIFF_FILE_HEADER = re.compile(r'^diff --git ', re.MULTILINE)
_DIFF_GIT_PATH_RE = re.compile(r'^diff --git a/(.+) b/')
_HUNK_HEADER_RE   = re.compile(r'^(@@[^@]*@@[^\n]*)', re.MULTILINE)

_DEFAULT_IGNORE_PATTERNS: list[str] = [
    # lock 파일
    "*.lock", "package-lock.json", "yarn.lock", "Pipfile.lock",
    "Gemfile.lock", "*.sum", "poetry.lock",
    # 생성/빌드
    "*.min.js", "*.min.css", "dist/*", "build/*",
    "*_pb2.py", "*_pb.py", "*.generated.*",
    # IDE / OS
    ".idea/*", ".vscode/*", "*.iml", ".DS_Store",
]


@dataclass
class DiffResult:
    content: str


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _split_into_file_diffs(raw_diff: str) -> list[str]:
    """raw diff를 파일별 diff 블록으로 분리한다."""
    boundaries = [m.start() for m in _DIFF_FILE_HEADER.finditer(raw_diff)]
    if not boundaries:
        return [raw_diff] if raw_diff.strip() else []
    file_diffs = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(raw_diff)
        file_diffs.append(raw_diff[start:end])
    return file_diffs


def _matches_ignore(filename: str, patterns: list[str]) -> bool:
    """filename이 패턴 중 하나라도 매칭되면 True."""
    return any(fnmatch(filename, pat) for pat in patterns)


def filter_file_diffs(raw_diff: str, extra_patterns: list[str]) -> tuple[str, int]:
    """파일 단위로 제외 패턴 매칭 + 바이너리 감지 후 필터링한다.

    Returns:
        (필터된 diff 문자열, 제외된 파일 수)
    """
    all_patterns = _DEFAULT_IGNORE_PATTERNS + extra_patterns
    file_diffs = _split_into_file_diffs(raw_diff)
    kept: list[str] = []
    skipped = 0
    for fd in file_diffs:
        m = _DIFF_GIT_PATH_RE.match(fd)
        if m:
            filename = m.group(1)
            if _matches_ignore(filename, all_patterns):
                skipped += 1
                continue
        if "Binary files" in fd:
            skipped += 1
            continue
        kept.append(fd)
    return "\n".join(kept), skipped


def omit_deletion_hunks(file_diff: str) -> str:
    """파일 diff에서 '+' 줄이 없는 순수 삭제 hunk를 제거한다.

    모든 hunk가 제거된 경우 빈 문자열을 반환한다(파일 전체 제외).
    """
    # 헤더: diff --git ... +++ b/... 줄까지 포함
    header_end = file_diff.find("\n@@")
    if header_end == -1:
        # hunk 없음 → 원본 반환
        return file_diff
    header = file_diff[: header_end + 1]  # 개행 포함

    # hunk 분리
    hunk_starts = [m.start() for m in _HUNK_HEADER_RE.finditer(file_diff, header_end)]
    if not hunk_starts:
        return file_diff

    hunks: list[str] = []
    for i, start in enumerate(hunk_starts):
        end = hunk_starts[i + 1] if i + 1 < len(hunk_starts) else len(file_diff)
        hunks.append(file_diff[start:end])

    kept_hunks: list[str] = []
    for hunk in hunks:
        lines = hunk.splitlines()
        has_addition = any(ln.startswith("+") and not ln.startswith("+++") for ln in lines)
        if has_addition:
            kept_hunks.append(hunk)

    if not kept_hunks:
        return ""
    return header + "".join(kept_hunks)


def _apply_omit_deletions(raw_diff: str) -> str:
    """raw diff 전체에 omit_deletion_hunks를 파일별로 적용한다."""
    file_diffs = _split_into_file_diffs(raw_diff)
    result: list[str] = []
    for fd in file_diffs:
        filtered = omit_deletion_hunks(fd)
        if filtered:
            result.append(filtered)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_primary_language(file_paths: list[str]) -> str:
    """파일 경로 목록에서 가장 많이 등장하는 언어를 반환한다. 알 수 없으면 빈 문자열."""
    counts: Counter = Counter()
    for path in file_paths:
        ext = os.path.splitext(path)[1].lower()
        if ext in _EXT_TO_LANG:
            counts[_EXT_TO_LANG[ext]] += 1
    return counts.most_common(1)[0][0] if counts else ""


def extract_changed_files(diff_content: str) -> list[str]:
    """diff 내용에서 변경된 파일 경로 목록을 반환한다."""
    file_diffs = _split_into_file_diffs(diff_content)
    paths = []
    for fd in file_diffs:
        m = _DIFF_GIT_PATH_RE.match(fd)
        if m:
            paths.append(m.group(1))
    return paths


def split_diff_into_chunks(content: str, max_chars: int) -> list[str]:
    """git diff를 파일 경계(diff --git)로 분할하여 max_chars 이하 청크 리스트로 반환."""
    boundaries = [m.start() for m in _DIFF_FILE_HEADER.finditer(content)]
    if not boundaries:
        return [content[:max_chars]]

    file_diffs = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(content)
        file_diffs.append(content[start:end])

    chunks = []
    current = ""
    for fd in file_diffs:
        if len(fd) > max_chars:
            cut = fd.rfind("\n", 0, max_chars)
            fd = (fd[:cut] + "\n# ...(truncated)") if cut > 0 else fd[:max_chars]
        if current and len(current) + len(fd) > max_chars:
            chunks.append(current)
            current = fd
        else:
            current += fd
    if current:
        chunks.append(current)
    return chunks


def extract_incremental_diff(workspace_path: str, mr_iid: str, oldrev: str) -> Optional[DiffResult]:
    """oldrev..HEAD 범위의 증분 diff를 추출한다. 실패 시 None 반환."""
    try:
        logger.info(f"🔍 [MR #{mr_iid}] 증분 diff 추출 중 ({oldrev[:8]}..HEAD)")
        result = subprocess.run(
            ["git", "diff", f"{oldrev}..HEAD"],
            cwd=workspace_path, capture_output=True, text=True
        )
        content = result.stdout
        if not content.strip():
            logger.info(f"[MR #{mr_iid}] 증분 diff 없음 (빈 변경)")
            return DiffResult(content="")
        extra = [p.strip() for p in settings.diff_ignore_patterns.split(",") if p.strip()]
        content, skipped = filter_file_diffs(content, extra)
        if skipped:
            logger.info(f"[MR #{mr_iid}] {skipped}개 파일 제외 (lock/binary/generated)")
        if settings.diff_omit_deletions:
            content = _apply_omit_deletions(content)
        return DiffResult(content=content)
    except Exception as e:
        logger.error(f"⚠️ [MR #{mr_iid}] 증분 diff 추출 에러: {str(e)}")
        return None


def extract_diff(workspace_path: str, mr_iid: str, source_branch: str, target_branch: str) -> Optional[DiffResult]:
    """target..source 간 diff를 추출한다. 실패 시 None 반환."""
    try:
        logger.info(f"🔍 [MR #{mr_iid}] {target_branch}와(과)의 Diff를 추출합니다.")
        subprocess.run(
            ["git", "fetch", "origin", target_branch],
            cwd=workspace_path, check=True, capture_output=True
        )
        result = subprocess.run(
            ["git", "diff", f"origin/{target_branch}...origin/{source_branch}"],
            cwd=workspace_path, capture_output=True, text=True
        )
        content = result.stdout

        if not content.strip():
            logger.info(f"[MR #{mr_iid}] diff 없음 (빈 변경)")
            return DiffResult(content="")

        # ① 파일 필터링
        extra = [p.strip() for p in settings.diff_ignore_patterns.split(",") if p.strip()]
        content, skipped = filter_file_diffs(content, extra)
        if skipped:
            logger.info(f"[MR #{mr_iid}] {skipped}개 파일 제외 (lock/binary/generated)")

        # ② 삭제 hunk 제거
        if settings.diff_omit_deletions:
            content = _apply_omit_deletions(content)

        return DiffResult(content=content)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8", errors="replace").strip() if isinstance(e.stderr, bytes) else (e.stderr or "")
        logger.error(f"❌ [MR #{mr_iid}] Diff 추출 실패 (코드 {e.returncode}): {error_msg}")
        return None
    except Exception as e:
        logger.error(f"⚠️ [MR #{mr_iid}] diff 추출 에러 발생: {str(e)}")
        return None
