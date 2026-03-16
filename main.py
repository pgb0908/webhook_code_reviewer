# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.
#
# This software is the confidential and proprietary information of TmaxSoft Co., Ltd. ("Confidential Information").
# You shall not disclose such Confidential Information and shall use it only in accordance with the terms of the license agreement you entered into with TmaxSoft Co., Ltd.

import copy
import logging
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from config import settings
from webhook.handler import router
from workspace.manager import ensure_workspace_base

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
LOG_LEVEL = getattr(logging, settings.log_level.upper(), logging.INFO)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, force=True)
logger = logging.getLogger(__name__)

app_import = "main:app"
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


if __name__ == "__main__":
    logger.info("🚀 Aider GitLab Webhook Server를 시작합니다.")
    uvicorn.run(
        app_import,
        host=settings.server_host,
        port=settings.server_port,
        reload=True,
        log_config=uvicorn_log_config,
    )
