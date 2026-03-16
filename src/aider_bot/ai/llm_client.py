# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.

import json
import logging
from typing import Optional

import requests

from aider_bot.config import settings

logger = logging.getLogger(__name__)


def _build_url(path: str) -> str:
    return f"{settings.remote_llm_base_url.rstrip('/')}/{path.lstrip('/')}"


def _candidate_urls() -> list[str]:
    base = settings.remote_llm_base_url.rstrip("/")
    candidates = [f"{base}/chat/completions"]

    if base.endswith("/v1"):
        candidates.append(f"{base[:-3].rstrip('/')}/chat/completions")
    else:
        candidates.append(f"{base}/v1/chat/completions")

    seen: set[str] = set()
    ordered: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _candidate_models() -> list[str]:
    model = settings.llm_client_model
    candidates = [model]

    if model.startswith("openai/"):
        candidates.append(model[len("openai/"):])

    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _extract_message_content(payload: dict) -> Optional[str]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")).strip())
        merged = "\n".join(part for part in parts if part).strip()
        return merged or None
    return None


def chat_completion(
    mr_iid: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.0,
) -> Optional[str]:
    headers = {"Content-Type": "application/json"}
    api_key = (settings.remote_llm_api_key or "").strip()
    if api_key and api_key.lower() != "dummy":
        headers["Authorization"] = f"Bearer {api_key}"

    candidate_urls = _candidate_urls()
    candidate_models = _candidate_models()
    logger.info("🤖 [MR #%s] llm-client 요청 후보 URL: %s", mr_iid, " -> ".join(candidate_urls))
    logger.info("🤖 [MR #%s] llm-client 요청 후보 모델: %s", mr_iid, " -> ".join(candidate_models))

    response = None
    last_error: Optional[requests.RequestException] = None
    for model_index, model in enumerate(candidate_models):
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "🧠 [MR #%s] llm-client request payload(model=%s):\n%s",
                mr_iid,
                model,
                json.dumps(payload, ensure_ascii=False, indent=2),
            )

        for url_index, url in enumerate(candidate_urls):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=settings.llm_timeout)
                response.raise_for_status()
                if model_index > 0 or url_index > 0:
                    logger.info("ℹ️ [MR #%s] llm-client 대체 요청 성공: model=%s, url=%s", mr_iid, model, url)
                break
            except requests.HTTPError as exc:
                last_error = exc
                status_code = exc.response.status_code if exc.response is not None else "unknown"
                body = ""
                if exc.response is not None:
                    body = exc.response.text or ""
                if status_code == 404 and url_index + 1 < len(candidate_urls):
                    logger.warning(
                        "⚠️ [MR #%s] llm-client 엔드포인트 404: %s, 대체 경로를 시도합니다",
                        mr_iid,
                        url,
                    )
                    continue
                if status_code == 404 and "does not exist" in body and model_index + 1 < len(candidate_models):
                    logger.warning(
                        "⚠️ [MR #%s] llm-client 모델 404: %s, 대체 모델을 시도합니다",
                        mr_iid,
                        model,
                    )
                    response = None
                    break
                logger.error("❌ [MR #%s] llm-client 요청 실패(%s): %s", mr_iid, status_code, exc)
                return None
            except requests.RequestException as exc:
                last_error = exc
                logger.error("❌ [MR #%s] llm-client 요청 실패: %s", mr_iid, exc)
                return None
        if response is not None:
            break
    else:
        logger.error(
            "❌ [MR #%s] llm-client 요청 실패. 시도한 엔드포인트: %s. 시도한 모델: %s. 마지막 오류: %s",
            mr_iid,
            ", ".join(candidate_urls),
            ", ".join(candidate_models),
            last_error,
        )
        return None

    try:
        data = response.json()
    except ValueError:
        logger.error("❌ [MR #%s] llm-client JSON 응답 파싱 실패", mr_iid)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("📥 [MR #%s] llm-client raw response:\n%s", mr_iid, response.text)
        return None

    content = _extract_message_content(data)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("📥 [MR #%s] llm-client 응답 전문:\n%s", mr_iid, content or "<empty>")

    if not content:
        logger.error("❌ [MR #%s] llm-client 응답 본문이 비어 있습니다", mr_iid)
        return None
    return content
