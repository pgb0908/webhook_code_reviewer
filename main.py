import os
import logging
import subprocess
import shutil
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
import requests
import uvicorn  # 추가됨: 서버 실행용
from dotenv import load_dotenv

# SRE를 위한 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI()
gitlab_token = os.getenv("GITLAB_TOKEN")
gitlab_host = os.getenv("GITLAB_HOST")
project_path = os.getenv("PROJECT_PATH")

# AnyAPI 프로젝트의 캐시 폴더가 저장될 기본 경로
WORKSPACE_BASE = os.environ.get("WORKSPACE_BASE", "/tmp/aider_workspaces")
os.makedirs(WORKSPACE_BASE, exist_ok=True)

# --- 1. GitLab API 코멘트 전송 헬퍼 함수 ---
def post_comment_to_gitlab(project_id: str, mr_iid: str, message: str):
    logger.info(f"aider 말하길:\n{message}")

    if not project_id or project_id == "None":
        logger.error(f"❌ [MR #{mr_iid}] project_id가 누락되어 코멘트를 전송할 수 없습니다.")
        return

    gitlab_token = os.getenv("GITLAB_TOKEN")
    # http:// 등을 제거한 순수 호스트 IP/도메인만 추출
    gitlab_host = os.getenv("GITLAB_HOST", "192.168.51.106").replace("http://", "").replace("https://", "")

    gitlab_api_url = f"http://{gitlab_host}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes"
    logger.info(f"📡 [MR #{mr_iid}] 코멘트 전송 시도: {gitlab_api_url}")

    headers = {"PRIVATE-TOKEN": gitlab_token}
    payload = {"body": f"🤖 **Aider AI 리뷰**\n\n{message}"}

    try:
        response = requests.post(gitlab_api_url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"✅ [MR #{mr_iid}] GitLab 코멘트 전송 성공")
    except Exception as e:
        # 에러 발생 시 실제 전송하려던 URL을 로그에 찍어 디버깅을 돕습니다.
        logger.error(f"❌ [MR #{mr_iid}] 코멘트 전송 실패 (URL: {gitlab_api_url}): {str(e)}")


# --- 2. 캐시 기반 Aider 에이전트 및 코드 리뷰(Diff) 실행 로직 ---
def run_aider_with_cache(project_id: str, mr_iid: str, source_branch: str, target_branch: str,
                             question: str = None):
    mr_workspace = os.path.join(WORKSPACE_BASE, f"anyapi_mr_{mr_iid}")
    repo_url = f"http://oauth2:{gitlab_token}@{gitlab_host}/{project_path}.git"
    logger.info(f"작업공간: {mr_workspace}")

    if not source_branch or not repo_url or not project_id:
        logger.error(f"❌ [MR #{mr_iid}] source_branch 혹은 repo_url이 없습니다 (GitLab Webhook 테스트 페이로드일 수 있습니다.)")
        return

    try:
        # [A] 캐싱 및 최신 코드 동기화
        if os.path.exists(os.path.join(mr_workspace, ".git")):
            logger.info(f"🔄 [MR #{mr_iid}] 기존 작업 공간 발견. Pull을 수행합니다.")
            logger.info(f"      - source_branch: {source_branch}")
            subprocess.run(["git", "fetch", "origin", source_branch], cwd=mr_workspace, check=True, capture_output=True)
            subprocess.run(["git", "checkout", source_branch], cwd=mr_workspace, check=True, capture_output=True)
            subprocess.run(["git", "pull", "origin", source_branch], cwd=mr_workspace, check=True, capture_output=True)
        else:
            logger.info(f"📥 [MR #{mr_iid}] 새 작업 공간 생성. Clone을 진행합니다.")
            # 기존에 빈 껍데기 폴더가 있다면 과감히 삭제하고 새로 만듦
            if os.path.exists(mr_workspace):
                shutil.rmtree(mr_workspace)
            os.makedirs(mr_workspace, exist_ok=True)
            logger.info(f"      - repo_url: {repo_url}")
            logger.info(f"      - source_branch: {source_branch}")
            subprocess.run(["git", "clone", "--branch", source_branch, repo_url, "."], cwd=mr_workspace, check=True,
                           capture_output=True)

        # [B] Target Branch 정보 가져오기 및 Git Diff 추출 (리뷰용)
        logger.info(f"🔍 [MR #{mr_iid}] {target_branch}와(과)의 Diff를 추출합니다.")
        subprocess.run(["git", "fetch", "origin", target_branch], cwd=mr_workspace, check=True, capture_output=True)
        
        diff_process = subprocess.run(
            ["git", "diff", f"origin/{target_branch}...origin/{source_branch}"],
            cwd=mr_workspace, capture_output=True, text=True
        )
        git_diff = diff_process.stdout
    except subprocess.CalledProcessError as e:
        # Git 명령어 실행 중 발생한 에러의 구체적인 메시지(stderr) 추출
        error_msg = e.stderr.decode('utf-8').strip() if isinstance(e.stderr, bytes) else e.stderr
        if not error_msg and hasattr(e, 'stdout'):
            error_msg = e.stdout.decode('utf-8').strip() if isinstance(e.stdout, bytes) else e.stdout

        logger.error(f"❌ [MR #{mr_iid}] Git 명령어 실패 (코드 {e.returncode}): {error_msg}")
        return
    except Exception as e:
        logger.error(f"⚠️ [MR #{mr_iid}] git-파이프라인 에러 발생: {str(e)}")
        return

    try:
        # [C] Aider CLI 프롬프트 구성 (질문 + Diff)
        logger.info(f"🧠 [MR #{mr_iid}] Aider 분석 및 답변 생성 중...")

        user_question = question if question else "이 Merge Request의 변경 사항에 대해 전반적인 코드 리뷰를 해줘."
        prompt = f"""
        너는 우리 팀의 수석 SRE이자 C++ 백엔드 전문가야.
        {user_question}
        
        아래는 이번 Merge Request의 코드 변경 사항(Diff)이야:
        ```diff
        {git_diff[:10000]} # 토큰 초과 방지를 위해 길이 제한 (필요시 조절)
        ```
        
        기존 프로젝트의 Repo Map과 위 변경 사항을 종합적으로 분석해서 명확하게 마크다운으로 답변해 줘.
        코드는 절대 직접 수정하지 마.
        """
        
        env = os.environ.copy()
        env["OPENAI_API_BASE"] = os.getenv("REMOTE_LLM_BASE_URL")
        env["OPENAI_API_KEY"] = os.getenv("REMOTE_LLM_API_KEY", "dummy")

        aider_command = [
            "aider",
            "--model", "openai/Qwen/Qwen3-Coder-30B-A3B-Instruct",
            "--api-key", "openai=none",
            "--no-auto-commits",
            "--exit",  # 답변 후 프로세스 종료 (필수)
            "--yes",  # 모든 프롬프트에 자동 'Yes' 응답
            "--message", prompt
        ]

        # logger를 통해 실행되는 전체 명령어를 확인해 보세요.
        logger.info(f"🚀 실행 명령어: {' '.join(aider_command)}")

        aider_process = subprocess.run(
            aider_command,
            cwd=mr_workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=60  # 10분
        )

        # [D] 결과 전송
        if aider_process.returncode == 0:
            post_comment_to_gitlab(project_id, mr_iid, aider_process.stdout)
        else:
            logger.error(f"❌ [MR #{mr_iid}] Aider 실행 에러: {aider_process.stderr}")

    except Exception as e:
        logger.error(f"⚠️ [MR #{mr_iid}] aider-파이프라인 에러 발생: {str(e)}")
        return


# --- 3. 디스크 정리 (MR 종료 시 캐시 삭제) 로직 ---
def cleanup_mr_workspace(mr_iid: str):
    mr_workspace = os.path.join(WORKSPACE_BASE, f"anyapi_mr_{mr_iid}")
    if os.path.exists(mr_workspace):
        try:
            shutil.rmtree(mr_workspace)
            logger.info(f"🧹 [MR #{mr_iid}] MR이 종료되어 캐시 폴더를 안전하게 삭제했습니다.")
        except Exception as e:
            logger.error(f"❌ [MR #{mr_iid}] 폴더 삭제 실패: {str(e)}")


# --- 4. Webhook 라우팅 ---
@app.post("/webhook")
async def gitlab_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    object_kind = payload.get("object_kind")
    project_id = str(
        payload.get("project_id") or
        payload.get("project", {}).get("id") or
        ""
    )

    logger.info(f"🔔 Webhook 수신 - Object Kind: {object_kind}, Project ID: {project_id}")

    # [이벤트 A] 댓글 멘션 시 -> 질의응답 실행
    if object_kind == "note":
        merge_request = payload.get("merge_request")
        if not merge_request:
            return {"status": "ignored"}

        comment_text = payload.get("object_attributes", {}).get("note", "").lower()
        if "@aider" not in comment_text:
            return {"status": "ignored"}

        mr_iid = str(merge_request.get("iid"))
        source_branch = merge_request.get("source_branch")
        target_branch = merge_request.get("target_branch", "main")
        clean_question = comment_text.replace("@aider", "").strip()

        logger.info(f"🔔 [MR #{mr_iid}] 멘션 감지. 답변 생성을 시작합니다.")
        background_tasks.add_task(run_aider_with_cache, project_id, mr_iid, source_branch, target_branch,
                                  clean_question)
        return {"status": "queued"}

    # [이벤트 B] MR 상태 변경 시 -> 자동 리뷰 또는 폴더 정리
    elif object_kind == "merge_request":
        mr_attributes = payload.get("object_attributes", {})
        mr_iid = str(mr_attributes.get("iid"))
        action = mr_attributes.get("action")  # open, update, close, merge 등
        state = mr_attributes.get("state")

        source_branch = mr_attributes.get("source_branch")
        target_branch = mr_attributes.get("target_branch", "main")

        # 1. MR이 새로 열리거나 커밋이 추가(update)되었을 때 -> 자동 코드 리뷰
        if action in ["open", "update"]:
            logger.info(f"🚀 [MR #{mr_iid}] 코드 변경({action}) 감지. 자동 리뷰를 시작합니다.")
            # question 파라미터를 생략(None)하여 자동 리뷰 모드로 실행
            background_tasks.add_task(run_aider_with_cache, project_id, mr_iid, source_branch, target_branch)
            return {"status": "auto_review_queued"}

        # 2. MR이 닫히거나 병합되었을 때 -> 디스크 정리
        if state in ["closed", "merged"] or action in ["close", "merge"]:
            logger.info(f"🗑️ [MR #{mr_iid}] MR 종료 감지. 정리 작업을 시작합니다.")
            background_tasks.add_task(cleanup_mr_workspace, mr_iid)
            return {"status": "cleanup_queued"}

        return {"status": "ignored"}

    return {"status": "ignored"}

# --- 5. 앱 실행(Main) 블록 ---
if __name__ == "__main__":
    load_dotenv()
    logger.info("🚀 Aider GitLab Webhook Server를 시작합니다.")
    # 0.0.0.0:8000 포트에서 실행. 운영 환경에서는 uvicorn 명령어 직접 실행을 권장합니다.
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
