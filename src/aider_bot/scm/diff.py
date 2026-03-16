# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import hashlib
import os
import re
import logging
import subprocess
from collections import Counter
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Optional

from aider_bot.config import settings

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
    source_sha: str = ""
    target_sha: str = ""


@dataclass
class FileDiff:
    path: str
    change_type: str
    content: str
    added_lines: int
    deleted_lines: int


@dataclass
class ReviewUnit:
    unit_id: str
    path: str
    change_type: str
    diff: str
    added_lines: int
    deleted_lines: int
    risk_score: int
    tags: list[str]
    related_paths: list[str]


@dataclass
class DiffLineRef:
    old_path: str
    new_path: str
    new_lines: list[int]


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


def _detect_change_type(file_diff: str) -> str:
    if "new file mode" in file_diff:
        return "added"
    if "deleted file mode" in file_diff:
        return "deleted"
    if re.search(r"^rename from ", file_diff, re.MULTILINE):
        return "renamed"
    return "modified"


def _extract_paths(file_diff: str) -> tuple[str, str]:
    old_path = ""
    new_path = ""
    for line in file_diff.splitlines():
        if line.startswith("--- "):
            value = line[4:].strip()
            old_path = value[2:] if value.startswith("a/") else value
        elif line.startswith("+++ "):
            value = line[4:].strip()
            new_path = value[2:] if value.startswith("b/") else value
    return old_path, new_path


def _count_changed_lines(file_diff: str) -> tuple[int, int]:
    added = 0
    deleted = 0
    for line in file_diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            deleted += 1
    return added, deleted


def _extract_related_paths(path: str, all_paths: list[str]) -> list[str]:
    base = os.path.splitext(os.path.basename(path))[0]
    directory = os.path.dirname(path)
    related: list[str] = []
    for candidate in all_paths:
        if candidate == path:
            continue
        if os.path.dirname(candidate) == directory:
            related.append(candidate)
            continue
        candidate_base = os.path.splitext(os.path.basename(candidate))[0]
        if candidate_base == base or candidate_base in {f"test_{base}", f"{base}_test"}:
            related.append(candidate)
    return related[:5]


def _score_review_unit(path: str, file_diff: str, added_lines: int, deleted_lines: int) -> tuple[int, list[str]]:
    path_lower = path.lower()
    diff_lower = file_diff.lower()
    score = min(40, added_lines + deleted_lines)
    tags: list[str] = []

    if any(token in path_lower for token in ("auth", "permission", "security", "login", "token")):
        score += 30
        tags.append("security")
    if any(token in diff_lower for token in ("transaction", "rollback", "commit", "select ", "update ", "delete from", "insert into")):
        score += 20
        tags.append("database")
    if any(token in diff_lower for token in ("thread", "lock", "mutex", "asyncio", "await ", "concurrent", "race")):
        score += 20
        tags.append("concurrency")
    if any(token in path_lower for token in ("api", "controller", "handler", "router", "endpoint")):
        score += 15
        tags.append("api")
    if any(token in path_lower for token in ("config", "settings", "deployment", "docker", "k8s", "helm")):
        score += 10
        tags.append("ops")
    if re.search(r"^\+.*(TODO|FIXME|XXX)", file_diff, re.MULTILINE):
        score += 10
        tags.append("todo")
    if path_lower.endswith(("_test.py", ".spec.ts", ".test.ts", "test.py")):
        score = max(5, score - 20)
        tags.append("test")

    return min(score, 100), tags


def _current_head_sha(workspace_path: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


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


def parse_file_diffs(raw_diff: str) -> list[FileDiff]:
    file_diffs = _split_into_file_diffs(raw_diff)
    parsed: list[FileDiff] = []
    for fd in file_diffs:
        m = _DIFF_GIT_PATH_RE.match(fd)
        if not m:
            continue
        path = m.group(1)
        change_type = _detect_change_type(fd)
        added_lines, deleted_lines = _count_changed_lines(fd)
        parsed.append(
            FileDiff(
                path=path,
                change_type=change_type,
                content=fd,
                added_lines=added_lines,
                deleted_lines=deleted_lines,
            )
        )
    return parsed


def build_diff_line_refs(raw_diff: str) -> dict[str, DiffLineRef]:
    refs: dict[str, DiffLineRef] = {}
    for fd in _split_into_file_diffs(raw_diff):
        m = _DIFF_GIT_PATH_RE.match(fd)
        if not m:
            continue

        fallback_path = m.group(1)
        old_path, new_path = _extract_paths(fd)
        key_path = new_path if new_path and new_path != "/dev/null" else fallback_path
        old_path = old_path if old_path and old_path != "/dev/null" else key_path
        new_path = new_path if new_path and new_path != "/dev/null" else key_path

        new_lines: list[int] = []
        current_old = 0
        current_new = 0
        in_hunk = False

        for line in fd.splitlines():
            if line.startswith("@@"):
                match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if not match:
                    in_hunk = False
                    continue
                current_old = int(match.group(1))
                current_new = int(match.group(2))
                in_hunk = True
                continue

            if not in_hunk:
                continue

            if line.startswith("+") and not line.startswith("+++"):
                new_lines.append(current_new)
                current_new += 1
            elif line.startswith("-") and not line.startswith("---"):
                current_old += 1
            else:
                current_old += 1
                current_new += 1

        refs[key_path] = DiffLineRef(old_path=old_path, new_path=new_path, new_lines=new_lines)
    return refs


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


def build_review_units(diff_content: str) -> list[ReviewUnit]:
    file_diffs = parse_file_diffs(diff_content)
    all_paths = [fd.path for fd in file_diffs]
    units: list[ReviewUnit] = []
    for fd in file_diffs:
        risk_score, tags = _score_review_unit(fd.path, fd.content, fd.added_lines, fd.deleted_lines)
        unit_hash = hashlib.sha1(f"{fd.path}:{fd.change_type}".encode("utf-8")).hexdigest()[:12]
        units.append(
            ReviewUnit(
                unit_id=f"{fd.path}#{unit_hash}",
                path=fd.path,
                change_type=fd.change_type,
                diff=fd.content,
                added_lines=fd.added_lines,
                deleted_lines=fd.deleted_lines,
                risk_score=risk_score,
                tags=tags,
                related_paths=_extract_related_paths(fd.path, all_paths),
            )
        )
    units.sort(key=lambda item: item.risk_score, reverse=True)
    return units


_SIMILARITY_RE = re.compile(r"similarity index (\d+)%")

_MAX_CONTEXT_FILES = 10


def rank_changed_files(diff_content: str, max_files: int = _MAX_CONTEXT_FILES) -> list[str]:
    """diff에서 실질적 변경이 큰 파일 순으로 상위 max_files개 경로를 반환한다.

    제외 대상:
    - similarity index >= 80% (rename/move)
    - _DEFAULT_IGNORE_PATTERNS 매칭 파일
    - Binary 파일
    """
    file_diffs = _split_into_file_diffs(diff_content)
    scored: list[tuple[str, int]] = []

    for fd in file_diffs:
        m = _DIFF_GIT_PATH_RE.match(fd)
        if not m:
            continue
        path = m.group(1)

        if _matches_ignore(path, _DEFAULT_IGNORE_PATTERNS):
            continue

        if "Binary files" in fd:
            continue

        sim = _SIMILARITY_RE.search(fd)
        if sim and int(sim.group(1)) >= 80:
            continue

        score = 0
        for line in fd.splitlines():
            if (line.startswith("+") and not line.startswith("+++")) or \
               (line.startswith("-") and not line.startswith("---")):
                score += 1

        scored.append((path, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [path for path, _ in scored[:max_files]]


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
            return DiffResult(content="", source_sha=_current_head_sha(workspace_path), target_sha=oldrev)
        extra = [p.strip() for p in settings.diff_ignore_patterns.split(",") if p.strip()]
        content, skipped = filter_file_diffs(content, extra)
        if skipped:
            logger.info(f"[MR #{mr_iid}] {skipped}개 파일 제외 (lock/binary/generated)")
        if settings.diff_omit_deletions:
            content = _apply_omit_deletions(content)
        return DiffResult(content=content, source_sha=_current_head_sha(workspace_path), target_sha=oldrev)
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
            return DiffResult(content="", source_sha=_current_head_sha(workspace_path), target_sha="")

        # ① 파일 필터링
        extra = [p.strip() for p in settings.diff_ignore_patterns.split(",") if p.strip()]
        content, skipped = filter_file_diffs(content, extra)
        if skipped:
            logger.info(f"[MR #{mr_iid}] {skipped}개 파일 제외 (lock/binary/generated)")

        # ② 삭제 hunk 제거
        if settings.diff_omit_deletions:
            content = _apply_omit_deletions(content)

        source_sha = _current_head_sha(workspace_path)
        target_result = subprocess.run(
            ["git", "rev-parse", f"origin/{target_branch}"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
        )
        target_sha = target_result.stdout.strip() if target_result.returncode == 0 else ""
        return DiffResult(content=content, source_sha=source_sha, target_sha=target_sha)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8", errors="replace").strip() if isinstance(e.stderr, bytes) else (e.stderr or "")
        logger.error(f"❌ [MR #{mr_iid}] Diff 추출 실패 (코드 {e.returncode}): {error_msg}")
        return None
    except Exception as e:
        logger.error(f"⚠️ [MR #{mr_iid}] diff 추출 에러 발생: {str(e)}")
        return None
