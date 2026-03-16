# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _cache_dir(workspace_path: str) -> str:
    return os.path.join(workspace_path, ".review_cache")


def _cache_path(workspace_path: str, source_sha: str) -> str:
    cache_key = source_sha or "working-tree"
    return os.path.join(_cache_dir(workspace_path), f"{cache_key}.json")


def load_review_cache(workspace_path: str, source_sha: str) -> dict[str, Any]:
    path = _cache_path(workspace_path, source_sha)
    if not os.path.exists(path):
        return {"units": {}}
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            data.setdefault("units", {})
            return data
    except Exception as exc:
        logger.warning("⚠️ review cache load 실패: %s", exc)
    return {"units": {}}


def save_review_cache(workspace_path: str, source_sha: str, data: dict[str, Any]) -> None:
    path = _cache_path(workspace_path, source_sha)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def get_cached_unit(cache_data: dict[str, Any], unit_id: str) -> Optional[dict[str, Any]]:
    units = cache_data.get("units", {})
    unit = units.get(unit_id)
    return unit if isinstance(unit, dict) else None


def upsert_cached_unit(cache_data: dict[str, Any], unit_id: str, payload: dict[str, Any]) -> None:
    cache_data.setdefault("units", {})
    cache_data["units"][unit_id] = payload
