# Copyright (c) 2026 TmaxSoft Co., Ltd.
# All rights reserved.

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

from aider_bot.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    ok: bool
    command: str
    output: str


def _tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _truncate(text: str, limit: int = 4000) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "\n... (truncated)"


def _run_command(workspace_path: str, mr_iid: str, command: list[str]) -> tuple[bool, str]:
    logger.info("🧪 [MR #%s] 검증 명령 실행: %s", mr_iid, " ".join(command))
    process = subprocess.run(
        command,
        cwd=workspace_path,
        capture_output=True,
        text=True,
        timeout=settings.validation_timeout,
    )
    output = "\n".join(part for part in [process.stdout, process.stderr] if part).strip()
    return process.returncode == 0, _truncate(output)


def _auto_detect_commands(workspace_path: str) -> list[list[str]]:
    def exists(path: str) -> bool:
        return os.path.exists(os.path.join(workspace_path, path))

    if exists("pom.xml") and _tool_exists("mvn"):
        return [["mvn", "-B", "-q", "-DskipTests", "compile"]]

    if exists("gradlew"):
        return [["bash", "./gradlew", "compileJava", "-x", "test"]]

    if exists("build.gradle") or exists("build.gradle.kts"):
        if _tool_exists("gradle"):
            return [["gradle", "compileJava", "-x", "test"]]

    if exists("CMakeLists.txt") and _tool_exists("cmake"):
        return [
            ["cmake", "-S", ".", "-B", "build"],
            ["cmake", "--build", "build"],
        ]

    return []


def run_validation(workspace_path: str, mr_iid: str) -> ValidationResult | None:
    explicit = settings.validation_command.strip()
    if explicit:
        ok, output = _run_command(workspace_path, mr_iid, ["bash", "-lc", explicit])
        return ValidationResult(ok=ok, command=explicit, output=output)

    commands = _auto_detect_commands(workspace_path)
    if commands:
        last_command = ""
        last_output = ""
        for command in commands:
            ok, output = _run_command(workspace_path, mr_iid, command)
            last_command = " ".join(command)
            last_output = output
            if not ok:
                return ValidationResult(ok=False, command=last_command, output=last_output)
        return ValidationResult(ok=True, command=last_command, output=last_output)

    logger.info(
        "ℹ️ [MR #%s] 자동 검증 명령을 찾지 못해 빌드 검증을 생략합니다. "
        "필요하면 VALIDATION_COMMAND를 설정하세요.",
        mr_iid,
    )
    return None
