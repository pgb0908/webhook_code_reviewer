import logging
import os
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
    workspace_base: str = "/tmp/aider_workspaces"
    aider_timeout: int = 600  # 10분
    diff_max_chars: int = 10000
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

    @property
    def gitlab_api_base(self) -> str:
        return f"http://{self.gitlab_host}/api/v4"

    def get_token(self, project_id: str) -> str | None:
        """프로젝트 ID에 해당하는 토큰을 반환한다. 없으면 None."""
        return self.project_tokens.get(str(project_id))

    def build_repo_url(self, token: str, project_path: str) -> str:
        """토큰 포함 git URL을 생성한다 — 절대 로그에 찍지 말 것."""
        return f"http://oauth2:{token}@{self.gitlab_host}/{project_path}.git"


# 앱 시작 시 즉시 유효성 검사 (import 시 실행)
settings = Settings()
