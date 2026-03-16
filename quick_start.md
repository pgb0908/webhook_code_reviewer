# Quick Start

## 1. 사전 요구사항

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (패키지·가상환경 관리)
- GitLab 인스턴스 (Self-hosted 또는 GitLab.com)
- OpenAI 호환 LLM API (Ollama, vLLM, OpenAI 등)

### uv 설치

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 2. 의존성 설치

```bash
# 가상환경 생성 및 의존성 설치
uv sync

# aider-chat 별도 설치 (requirements.txt에서 제외된 패키지)
uv pip install aider-chat
```

> `uv sync`는 프로젝트 루트의 `requirements.txt`를 읽어 `.venv`를 자동 생성합니다.

현재 실제 애플리케이션 소스는 `src/aider_bot/` 아래에 있습니다.
- 실행 엔트리포인트: `main.py`
- 형상관리 대상 애플리케이션 코드: `src/aider_bot/`
- 패키지 실행도 가능: `PYTHONPATH=src uv run python -m aider_bot`

---

## 3. 환경변수 설정

프로젝트 루트에 `.env` 파일을 생성합니다.

GitLab 프로젝트 ID는 프로젝트 홈 > Settings > General 에서 확인
![img.png](images/img.png)

```dotenv
# GitLab 서버 주소 (http:// 또는 https:// 제외)
GITLAB_HOST=gitlab.example.com

# LLM 설정
REMOTE_LLM_BASE_URL=http://localhost:11434/v1
REMOTE_LLM_MODEL=openai/qwen2.5-coder:32b   # 기본값(하위호환)
LLM_CLIENT_MODEL=qwen2.5-coder:32b          # 구조화용 llm-client 모델명
AIDER_MODEL=openai/qwen2.5-coder:32b        # aider/litellm용 모델명
REMOTE_LLM_API_KEY=dummy  # 키가 필요없으면 그대로 둠

# 프로젝트별 GitLab Access Token
# GitLab 프로젝트 ID는 프로젝트 홈 > Settings > General 에서 확인
PROJECT_TOKEN_42=glpat-xxxxxxxxxxxxxxxxxxxx
PROJECT_TOKEN_99=glpat-yyyyyyyyyyyyyyyyyyyy

# (선택) push 리뷰 전 검증 명령
# 비우면 Maven/Gradle/CMake를 자동 감지
VALIDATION_COMMAND=
VALIDATION_TIMEOUT=180

# (선택) 봇 계정명 - 자기 멘션 무한루프 방지
BOT_USERNAME=aider-bot
```

### GitLab Access Token 발급

1. GitLab > 프로젝트 > **Settings > Access Tokens**
2. Token name: 임의 이름 (예: `aider-bot`)
3. Role: **Developer** 이상
4. Scopes: **api** 체크
5. **Create project access token** 클릭
6. 발급된 토큰을 `.env`의 `PROJECT_TOKEN_{project_id}` 에 입력

> 프로젝트 ID는 GitLab 프로젝트 홈 화면 상단 프로젝트 이름 아래에 표시됩니다.

![img_1.png](images/img_1.png)
![img_2.png](images/img_2.png)

---

## 4. GitLab Webhook 등록

서버를 실행하기 **전에** Webhook을 미리 등록해 두어도 됩니다.

1. GitLab 프로젝트 > **Settings > Webhooks > Add new webhook**
2. 아래와 같이 입력:

| 항목 | 값 |
|------|----|
| URL | `http://{서버IP}:8000/webhook` |
| Trigger | **Merge request events**, **Comments** 모두 체크 |
| SSL verification | 내부망이면 비활성화 가능 |

3. **Add webhook** 클릭

> 서버 실행 후 **Edit > Test > Merge request events** 로 연결 확인을 권장합니다.

---

## 5. 서버 실행

### 기본 실행 (포그라운드)

```bash
uv run python main.py

# 또는 패키지 실행
PYTHONPATH=src uv run python -m aider_bot
```

서버가 정상 기동되면:

```
INFO - 🚀 Aider GitLab Webhook Server를 시작합니다.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

### 터미널 종료 후에도 계속 실행 (nohup)

```bash
nohup uv run python main.py > aider_bot.log 2>&1 &
echo "PID: $!"
```

- 로그는 `aider_bot.log` 파일에 저장됩니다.
- 프로세스 ID(PID)를 메모해 두면 나중에 종료할 때 사용할 수 있습니다.

**프로세스 확인:**

```bash
ps aux | grep "main.py"
```

**프로세스 종료:**

```bash
kill {PID}
```

### tmux를 이용한 실행 (권장)

`nohup`보다 로그를 실시간으로 확인하기 편합니다.

```bash
# tmux 세션 생성
tmux new-session -d -s aider-bot

# 세션 안에서 서버 실행
tmux send-keys -t aider-bot 'uv run python main.py' Enter

# 세션 연결 (실시간 로그 확인)
tmux attach -t aider-bot

# 세션 분리 (서버는 계속 실행)
# Ctrl+B, D
```

---

## 6. 동작 확인

### 자동 MR 리뷰

GitLab에서 MR을 새로 생성하면 자동으로 AI 리뷰가 시작됩니다.

```
로그 예시:
INFO - 🆕 [MR #5] MR 생성 감지. overview 보고서 작성을 시작합니다.
INFO - 🧠 [MR #5] diff 청크 수: 1
INFO - 📡 [MR #5] MR overview 수정 시도: ...
INFO - ✅ [MR #5] MR overview 수정 성공
```

완료되면 MR 제목과 설명이 AI 생성 보고서로 교체됩니다.

현재 MR 리뷰 파이프라인은 아래 순서로 동작합니다.
1. Webhook 수신
2. 저장소 sync 및 diff 추출
3. diff를 `review unit` 단위로 정리
4. 각 `review unit`에 대해 `aider` 리뷰 요청
5. review 결과 취합
6. `llm-client` 구조화
7. GitLab MR 제목/설명 반영

### Push 코드리뷰

MR이 열린 상태에서 새 커밋을 push하면 자동으로 증분 리뷰가 수행됩니다.

- 증분 diff(`oldrev..HEAD`)만 분석
- 작은 변경이어도 최소 1개 unit은 deep review 수행
- 가능하면 GitLab diff discussion(inline)으로 게시
- 빌드/컴파일 검증이 실패하면 그 결과를 우선 코멘트로 게시

### 질의응답 (@aider 멘션)

MR 코멘트에 `@aider`를 포함해 질문합니다.

```
@aider 이 함수에서 메모리 누수 가능성이 있나요?
@aider handle_client 함수의 에러 처리가 적절한지 검토해줘
```

```
로그 예시:
INFO - 🔔 [MR #5] 멘션 감지. 답변 생성을 시작합니다.
INFO - 🧠 [MR #5] 질문에 대한 응답 생성 중...
INFO - ✅ [MR #5] GitLab 코멘트 전송 성공
```

`@aider` 응답은 가능하면 질문이 달린 같은 discussion thread에 reply로 남기고,
discussion 식별자를 찾지 못한 경우에만 일반 MR 코멘트로 폴백합니다.

---

## 7. 자주 발생하는 문제

### Webhook이 도달하지 않음

- 서버가 GitLab에서 접근 가능한 IP/포트인지 확인
- 방화벽 또는 포트 포워딩 설정 확인
- GitLab Webhook 페이지에서 최근 전송 로그 확인 (**Edit > Recent Deliveries**)

### "No token for project_id=N" 에러

```
❌ [MR #5] No token for project_id=N. Add PROJECT_TOKEN_N= to .env
```

→ `.env`에 `PROJECT_TOKEN_{N}=glpat-xxxx` 추가 후 서버 재시작

### Aider 타임아웃

```
❌ [MR #5] Aider 타임아웃 (600초 초과)
```

→ `.env`에 `AIDER_TIMEOUT=900` 등으로 늘리거나, `DIFF_IGNORE_PATTERNS`를 조정해 불필요한 파일을 제외

### LLM 연결 실패

```
❌ [MR #5] LLM 연결 실패
```

→ `REMOTE_LLM_BASE_URL`, `LLM_CLIENT_MODEL`, `AIDER_MODEL`이 올바른지 확인

### Push 리뷰에서 빌드 실패를 놓침

- 프로젝트마다 검증 명령이 다르면 `.env`에 `VALIDATION_COMMAND`를 직접 지정하는 것이 가장 정확합니다.
- 예시:

```dotenv
VALIDATION_COMMAND=mvn -B -q -DskipTests compile
VALIDATION_COMMAND=./gradlew compileJava -x test
VALIDATION_COMMAND=npm run build
VALIDATION_COMMAND=pytest -q
```

---

## 8. Ollama 연동 예시

```bash
# Ollama 설치 후 모델 다운로드
ollama pull qwen2.5-coder:32b

# .env 설정
REMOTE_LLM_BASE_URL=http://localhost:11434/v1
REMOTE_LLM_MODEL=openai/qwen2.5-coder:32b
LLM_CLIENT_MODEL=qwen2.5-coder:32b
AIDER_MODEL=openai/qwen2.5-coder:32b
REMOTE_LLM_API_KEY=dummy
```
