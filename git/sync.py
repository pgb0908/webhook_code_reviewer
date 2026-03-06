# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import os
import shutil
import logging
import subprocess

logger = logging.getLogger(__name__)


def sync_repository(workspace_path: str, mr_iid: str, source_branch: str, repo_url: str) -> bool:
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
                ["git", "clone", "--branch", source_branch, repo_url, "."],
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
