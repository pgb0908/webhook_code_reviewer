import os
import re
import shutil
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_DIFF_FILE_HEADER = re.compile(r'^diff --git ', re.MULTILINE)


@dataclass
class DiffResult:
    content: str


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
            fd = fd[:max_chars]
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
        return DiffResult(content=content)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8", errors="replace").strip() if isinstance(e.stderr, bytes) else (e.stderr or "")
        logger.error(f"❌ [MR #{mr_iid}] Diff 추출 실패 (코드 {e.returncode}): {error_msg}")
        return None
    except Exception as e:
        logger.error(f"⚠️ [MR #{mr_iid}] diff 추출 에러 발생: {str(e)}")
        return None
