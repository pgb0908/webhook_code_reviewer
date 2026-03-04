import logging
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from config import settings
from webhook.handler import router
from workspace.manager import ensure_workspace_base

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app_import = "main:app"

from fastapi import FastAPI
app = FastAPI(title="Aider GitLab Code Review Bot")
app.include_router(router)
ensure_workspace_base()


if __name__ == "__main__":
    logger.info("🚀 Aider GitLab Webhook Server를 시작합니다.")
    uvicorn.run(app_import, host=settings.server_host, port=settings.server_port, reload=True)
