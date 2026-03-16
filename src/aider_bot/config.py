# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import logging
import os
from pprint import pformat
from typing import Optional
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # 필수 환경변수
    gitlab_host: str
    remote_llm_base_url: str
    remote_llm_model: str

    # 선택 환경변수
    remote_llm_api_key: str = "dummy"
    log_level: str = "INFO"
    workspace_base: str = "/tmp/aider_workspaces"
    aider_timeout: int = 600  # 10분
    llm_timeout: int = 120
    validation_command: str = ""
    validation_timeout: int = 180
    max_deep_review_units: int = 12
    max_parallel_reviews: int = 3
    comment_max_context_files: int = 3
    comment_exclude_extensions: str = ".sh,.yml,.yaml,.json,.toml,.ini,.conf,.env"
    diff_ignore_patterns: str = ""   # 추가 제외 패턴 (쉼표 구분 glob). 예: "*.sum,dist/*"
    diff_omit_deletions: bool = True  # 삭제 전용 hunk 제거 여부
    bot_username: str = ""  # 봇 GitLab 계정명 (설정 시 봇 자신의 멘션 무시)
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # 프로젝트별 토큰 (PROJECT_TOKEN_* 환경변수에서 자동 수집)
    project_tokens: dict = {}

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def collect_project_tokens(cls, values: dict) -> dict:
        prefix = "PROJECT_TOKEN_"
        tokens = {
            key[len(prefix):]: val.strip()
            for key, val in os.environ.items()
            if key.startswith(prefix) and val.strip()
        }
        if tokens:
            logger.info(f"Loaded tokens for project IDs: {list(tokens.keys())}")
        values["project_tokens"] = tokens
        return values

    @field_validator("gitlab_host")
    @classmethod
    def strip_scheme(cls, v: str) -> str:
        return v.replace("https://", "").replace("http://", "")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, v: str) -> str:
        level = str(v or "INFO").upper()
        valid = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in valid:
            raise ValueError(f"log_level must be one of {sorted(valid)}")
        return level

    @property
    def gitlab_api_base(self) -> str:
        return f"http://{self.gitlab_host}/api/v4"

    @property
    def aider_model(self) -> str:
        return (os.environ.get("AIDER_MODEL") or self.remote_llm_model).strip()

    @property
    def llm_client_model(self) -> str:
        return (os.environ.get("LLM_CLIENT_MODEL") or self.remote_llm_model).strip()

    def get_token(self, project_id: str) -> Optional[str]:
        """프로젝트 ID에 해당하는 토큰을 반환한다. 없으면 None."""
        return self.project_tokens.get(str(project_id))

    def build_repo_url(self, token: str, project_path: str) -> str:
        """토큰 포함 git URL을 생성한다 — 절대 로그에 찍지 말 것."""
        return f"http://oauth2:{token}@{self.gitlab_host}/{project_path}.git"

    def masked_summary(self) -> str:
        """민감 정보를 제외한 설정 요약 문자열을 반환한다."""
        token_ids = sorted(str(project_id) for project_id in self.project_tokens.keys())
        token_preview = {
            project_id: self._mask_secret(self.project_tokens.get(project_id, ""))
            for project_id in token_ids
        }
        summary = {
            "gitlab_host": self.gitlab_host,
            "remote_llm_base_url": self.remote_llm_base_url,
            "remote_llm_model": self.remote_llm_model,
            "aider_model": self.aider_model,
            "llm_client_model": self.llm_client_model,
            "remote_llm_api_key": self._mask_secret(self.remote_llm_api_key),
            "log_level": self.log_level,
            "workspace_base": self.workspace_base,
            "aider_timeout": self.aider_timeout,
            "llm_timeout": self.llm_timeout,
            "validation_command": self.validation_command or "<auto>",
            "validation_timeout": self.validation_timeout,
            "max_deep_review_units": self.max_deep_review_units,
            "max_parallel_reviews": self.max_parallel_reviews,
            "comment_max_context_files": self.comment_max_context_files,
            "comment_exclude_extensions": self.comment_exclude_extensions,
            "diff_ignore_patterns": self.diff_ignore_patterns,
            "diff_omit_deletions": self.diff_omit_deletions,
            "bot_username": self.bot_username or "<unset>",
            "server_host": self.server_host,
            "server_port": self.server_port,
            "project_token_ids": token_ids,
            "project_tokens_preview": token_preview,
        }
        return pformat(summary, sort_dicts=False)

    @staticmethod
    def _mask_secret(value: str) -> str:
        if not value:
            return "<unset>"
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}...{value[-4:]}"


# 앱 시작 시 즉시 유효성 검사 (import 시 실행)
settings = Settings()
