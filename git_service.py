import os
import re
import shutil
import logging
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Optional

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


def sync_repository(settings, workspace_path: str, mr_iid: str, source_branch: str) -> bool:
    """저장소를 클론하거나 최신 상태로 동기화한다. 성공 시 True 반환."""
    try:
        if os.path.exists(os.path.join(workspace_path, ".git")):
            logger.info(f"🔄 [MR #{mr_iid}] 기존 작업 공간 발견. Pull을 수행합니다.")
            logger.info(f"      - source_branch: {source_branch}")
            subprocess.run(
                ["git", "fetch", "origin", source_branch],
                cwd=workspace_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "checkout", source_branch],
                cwd=workspace_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "pull", "origin", source_branch],
                cwd=workspace_path, check=True, capture_output=True
            )
        else:
            logger.info(f"📥 [MR #{mr_iid}] 새 작업 공간 생성. Clone을 진행합니다.")
            if os.path.exists(workspace_path):
                shutil.rmtree(workspace_path)
            os.makedirs(workspace_path, exist_ok=True)
            # repo_url을 로그에 찍지 않음 (토큰 노출 방지)
            logger.info(f"      - source_branch: {source_branch}")
            subprocess.run(
                ["git", "clone", "--branch", source_branch, settings.repo_url_template, "."],
                cwd=workspace_path, check=True, capture_output=True
            )
        return True
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8", errors="replace").strip() if isinstance(e.stderr, bytes) else (e.stderr or "")
        logger.error(f"❌ [MR #{mr_iid}] Git 명령어 실패 (코드 {e.returncode}): {error_msg}")
        return False
    except Exception as e:
        logger.error(f"⚠️ [MR #{mr_iid}] git 동기화 에러 발생: {str(e)}")
        return False


def extract_diff(settings, workspace_path: str, mr_iid: str, source_branch: str, target_branch: str) -> Optional[DiffResult]:
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
