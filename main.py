import logging
import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request

load_dotenv()

from config import settings
from webhook_handler import route_webhook
from workspace_manager import ensure_workspace_base

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Aider GitLab Code Review Bot")
ensure_workspace_base(settings)


@app.post("/webhook")
async def gitlab_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    return route_webhook(payload, settings, background_tasks.add_task)


if __name__ == "__main__":
    logger.info("🚀 Aider GitLab Webhook Server를 시작합니다.")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
