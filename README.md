# Aider GitLab Code Review Bot

GitLab Merge Request에 Aider AI 코드 리뷰를 자동으로 수행하는 Webhook 서버입니다.

## 주요 기능

| 기능 | 트리거 | 동작 |
|------|--------|------|
| **자동 MR 리뷰** | MR 생성(`open`) | diff를 분석해 MR 제목·설명을 AI 생성 보고서로 교체 |
| **질의응답** | MR 코멘트에 `@aider` 멘션 | 코드 기반으로 질문에 한국어 답변 |

## 아키텍처

```
GitLab Webhook
      │
      ▼
 FastAPI Server (POST /webhook)
      │
      ├─ MR open ──► sync → diff → overview 생성 → MR 제목/설명 교체
      │
      └─ @aider 멘션 ──► (sync) → comment 생성 → MR 코멘트 전송
```

### 디렉토리 구조

```
aider/
├── main.py                  # FastAPI 앱 진입점
├── config.py                # 환경변수 설정 (Pydantic Settings)
├── webhook/
│   ├── handler.py           # Webhook 수신 및 이벤트 분기
│   └── tasks.py             # overview / comment 파이프라인
├── git/
│   ├── sync.py              # 저장소 clone/pull
│   └── diff.py              # diff 추출 및 청크 분할
├── gitlab/
│   └── client.py            # GitLab API (코멘트, MR 수정)
├── ai/
│   ├── overview.py          # MR overview 생성 (Map-Reduce)
│   ├── comment.py           # 질의응답 생성
│   └── shared/
│       ├── subprocess.py    # Aider CLI subprocess 실행
│       └── output.py        # YAML 파싱 및 마크다운 렌더링
└── workspace/
    └── manager.py           # 작업 디렉토리 관리
```

## 환경변수

### 필수

| 변수 | 설명 | 예시 |
|------|------|------|
| `GITLAB_HOST` | GitLab 서버 주소 (scheme 제외) | `gitlab.example.com` |
| `REMOTE_LLM_BASE_URL` | LLM API Base URL | `http://localhost:11434/v1` |
| `REMOTE_LLM_MODEL` | 사용할 모델명 (aider 형식) | `openai/qwen2.5-coder:32b` |
| `PROJECT_TOKEN_{project_id}` | 프로젝트별 GitLab Access Token | `PROJECT_TOKEN_42=glpat-xxxx` |

> 프로젝트 토큰은 여러 개 설정 가능합니다. `project_id`는 GitLab 프로젝트의 숫자 ID입니다.

### 선택

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `REMOTE_LLM_API_KEY` | `dummy` | LLM API 키 (불필요한 경우 생략) |
| `WORKSPACE_BASE` | `/tmp/aider_workspaces` | 저장소 클론 기본 경로 |
| `AIDER_TIMEOUT` | `600` | Aider 실행 타임아웃 (초) |
| `DIFF_MAX_CHARS` | `10000` | diff 최대 크기 (초과 시 청크 분할) |
| `DIFF_IGNORE_PATTERNS` | `` | 추가 제외 파일 패턴 (쉼표 구분, glob) |
| `DIFF_OMIT_DELETIONS` | `true` | 삭제 전용 hunk 제거 여부 |
| `BOT_USERNAME` | `` | 봇 GitLab 계정명 (설정 시 자기 멘션 무시) |
| `SERVER_HOST` | `0.0.0.0` | 서버 바인딩 주소 |
| `SERVER_PORT` | `8000` | 서버 포트 |

## 설치

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. Aider 설치 (별도)
pip install aider-chat

# 3. 환경변수 설정
cp .env.example .env
# .env 파일 편집
```

## 실행

```bash
python main.py
```

또는 uvicorn으로 직접 실행:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## GitLab Webhook 설정

1. GitLab 프로젝트 > **Settings > Webhooks**
2. URL: `http://{서버주소}:8000/webhook`
3. Trigger 체크:
   - **Merge request events**
   - **Comments**
4. **Add webhook** 클릭

## 사용 방법

### 자동 MR 리뷰

MR을 생성하면 자동으로 다음이 수행됩니다:
- MR 제목이 AI가 생성한 한국어 제목으로 교체
- MR 설명이 변경 개요, 파일별 변경 내용, 리뷰 포인트를 포함한 보고서로 교체

### @aider 멘션

MR 코멘트에서 `@aider`를 멘션하면 코드 기반으로 답변합니다.

```
@aider 이 코드에서 메모리 누수 가능성이 있는 부분을 알려줘
@aider handle_client 함수의 에러 처리가 적절한지 검토해줘
```

## 대규모 diff 처리 (Map-Reduce)

diff 크기가 `DIFF_MAX_CHARS`를 초과하면 파일 경계로 청크 분할 후 Map-Reduce 방식으로 처리합니다:

1. **Map**: 청크별 개별 분석
2. **Reduce**: 분석 결과 취합 후 최종 overview 생성

## 주의사항

- GitLab Access Token에는 `api` 스코프가 필요합니다.
- 봇 계정의 무한 멘션 루프 방지를 위해 `BOT_USERNAME` 설정을 권장합니다.
- LLM 응답 품질은 모델 성능에 따라 달라집니다. `DIFF_MAX_CHARS`를 모델의 컨텍스트 크기에 맞게 조정하세요.
