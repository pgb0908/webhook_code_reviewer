# Quick Start

## 1. 사전 요구사항

- Python 3.10+
- GitLab 인스턴스 (Self-hosted 또는 GitLab.com)
- OpenAI 호환 LLM API (Ollama, vLLM, OpenAI 등)

---

## 2. 설치

```bash
pip install -r requirements.txt
pip install aider-chat
```

---

## 3. 환경변수 설정

프로젝트 루트에 `.env` 파일을 생성합니다.

```dotenv
# GitLab 서버 주소 (http:// 또는 https:// 제외)
GITLAB_HOST=gitlab.example.com

# LLM 설정
REMOTE_LLM_BASE_URL=http://localhost:11434/v1
REMOTE_LLM_MODEL=openai/qwen2.5-coder:32b
REMOTE_LLM_API_KEY=dummy  # 키가 필요없으면 그대로 둠

# 프로젝트별 GitLab Access Token
# GitLab 프로젝트 ID는 프로젝트 홈 > Settings > General 에서 확인
PROJECT_TOKEN_42=glpat-xxxxxxxxxxxxxxxxxxxx
PROJECT_TOKEN_99=glpat-yyyyyyyyyyyyyyyyyyyy

# (선택) 봇 계정명 - 자기 멘션 무한루프 방지
BOT_USERNAME=aider-bot
```

### GitLab Access Token 발급

1. GitLab > User Settings > Access Tokens
2. 또는 GitLab > Project > Settings > Access Tokens
3. Scopes: **api** 체크
4. 발급된 토큰을 `PROJECT_TOKEN_{project_id}` 에 입력

---

## 4. 서버 실행

```bash
python main.py
```

서버가 정상 기동되면:

```
INFO - 🚀 Aider GitLab Webhook Server를 시작합니다.
INFO - Uvicorn running on http://0.0.0.0:8000
```

---

## 5. GitLab Webhook 등록

1. GitLab 프로젝트 > **Settings > Webhooks > Add new webhook**
2. 아래와 같이 입력:

| 항목 | 값 |
|------|----|
| URL | `http://{서버IP}:8000/webhook` |
| Trigger | **Merge request events**, **Comments** 체크 |
| SSL verification | 필요시 비활성화 |

3. **Add webhook** 클릭 후 **Test > Merge request events** 로 연결 확인

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

### 질의응답

MR 코멘트에 `@aider`를 포함해 질문합니다.

```
@aider 이 함수에서 메모리 누수 가능성이 있나요?
```

```
로그 예시:
INFO - 🔔 [MR #5] 멘션 감지. 답변 생성을 시작합니다.
INFO - 🧠 [MR #5] 질문에 대한 응답 생성 중...
INFO - ✅ [MR #5] GitLab 코멘트 전송 성공
```

---

## 7. 자주 발생하는 문제

### Webhook이 도달하지 않음

- 서버가 GitLab에서 접근 가능한 IP/포트인지 확인
- 방화벽 또는 포트 포워딩 설정 확인
- GitLab Webhook 페이지에서 최근 전송 로그 확인 (Edit > Recent Deliveries)

### "No token for project_id=N" 에러

```
❌ [MR #5] No token for project_id=N. Add PROJECT_TOKEN_N= to .env
```

→ `.env`에 `PROJECT_TOKEN_{N}=glpat-xxxx` 추가 후 서버 재시작

### Aider 타임아웃

```
❌ [MR #5] Aider 타임아웃 (600초 초과)
```

→ `.env`에 `AIDER_TIMEOUT=900` 등으로 늘리거나, `DIFF_MAX_CHARS`를 줄여 처리량 감소

### LLM 연결 실패

```
❌ [MR #5] LLM 연결 실패
```

→ `REMOTE_LLM_BASE_URL`과 `REMOTE_LLM_MODEL`이 올바른지, LLM 서버가 실행 중인지 확인

---

## 8. Ollama 연동 예시

```bash
# Ollama 설치 후 모델 다운로드
ollama pull qwen2.5-coder:32b

# .env 설정
REMOTE_LLM_BASE_URL=http://localhost:11434/v1
REMOTE_LLM_MODEL=openai/qwen2.5-coder:32b
REMOTE_LLM_API_KEY=dummy
```
