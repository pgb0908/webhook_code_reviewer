# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.

import copy
import logging

import uvicorn
from fastapi import FastAPI

from aider_bot.config import settings
from aider_bot.webhook.handler import router
from aider_bot.webhook.context import ensure_workspace_base

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
LOG_LEVEL = getattr(logging, settings.log_level.upper(), logging.INFO)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, force=True)
logger = logging.getLogger(__name__)

uvicorn_log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
uvicorn_log_config["formatters"]["default"] = {
    "()": "logging.Formatter",
    "fmt": LOG_FORMAT,
}
uvicorn_log_config["formatters"]["access"] = {
    "()": "uvicorn.logging.AccessFormatter",
    "fmt": f"{LOG_FORMAT} - %(client_addr)s - \"%(request_line)s\" %(status_code)s",
}
uvicorn_log_config["loggers"]["uvicorn"]["level"] = settings.log_level
uvicorn_log_config["loggers"]["uvicorn.error"]["level"] = settings.log_level
uvicorn_log_config["loggers"]["uvicorn.access"]["level"] = settings.log_level

app = FastAPI(title="Aider GitLab Code Review Bot")
app.include_router(router)
ensure_workspace_base()


@app.on_event("startup")
async def log_startup_config() -> None:
    logger.info("⚙️ 현재 설정값:\n%s", settings.masked_summary())


def run() -> None:
    logger.info("🚀 Aider GitLab Webhook Server를 시작합니다.")
    uvicorn.run(
        "aider_bot.app:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=True,
        log_config=uvicorn_log_config,
    )
