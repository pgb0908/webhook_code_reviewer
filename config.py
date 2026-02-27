import logging
from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # 필수 환경변수
    gitlab_token: str
    gitlab_host: str
    project_path: str
    remote_llm_base_url: str

    # 선택 환경변수
    remote_llm_api_key: str = "dummy"
    workspace_base: str = "/tmp/aider_workspaces"
    aider_model: str = "openai/Qwen/Qwen3-Coder-30B-A3B-Instruct"
    aider_timeout: int = 600  # 10분
    diff_max_chars: int = 10000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @field_validator("gitlab_host")
    @classmethod
    def strip_scheme(cls, v: str) -> str:
        return v.replace("https://", "").replace("http://", "")

    @property
    def gitlab_api_base(self) -> str:
        return f"http://{self.gitlab_host}/api/v4"

    @property
    def repo_url_template(self) -> str:
        """토큰 포함 git URL — 로그에 찍지 말 것"""
        return f"http://oauth2:{self.gitlab_token}@{self.gitlab_host}/{self.project_path}.git"


# 앱 시작 시 즉시 유효성 검사 (import 시 실행)
settings = Settings()
