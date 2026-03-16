# Aider GitLab Code Review Bot

GitLab Merge Request에 Aider AI 코드 리뷰를 자동으로 수행하는 Webhook 서버입니다.

## 주요 기능

| 기능 | 트리거 | 동작 |
|------|--------|------|
| **자동 MR 리뷰** | MR 생성(`open`) | diff를 review unit으로 분해하고 위험도 기반으로 리뷰한 뒤 MR 제목·설명을 AI 보고서로 교체 |
| **Push 코드리뷰** | MR에 새 커밋 push(`update`) | 증분 diff를 review unit으로 분해해 선택적 리뷰 후 MR 코멘트로 게시 |
| **질의응답** | MR 코멘트에 `@aider` 멘션 | aider 자유형 답변을 생성하고 llm-client가 구조화 후 한국어 코멘트로 렌더링 |
| **자동 정리** | MR 종료(`close`/`merge`) | 워크스페이스 디렉토리 삭제 |

## 아키텍처

```
GitLab Webhook
      │
      ▼
FastAPI Server (POST /webhook)
      │
      ├─ MR open ──► sync → diff(전체) → review unit 생성/점수화
      │                          │
      │                          ├─ high risk unit → aider 자유형 review
      │                          ├─ llm-client → structured finding
      │                          ├─ finding cache 저장
      │                          └─ finding 합성 → aider overview 초안 → llm-client 구조화 → MR 제목/설명 교체
      │
      ├─ MR update(push) ──► sync → 증분 diff(oldrev..HEAD) → review unit 생성/점수화
      │                               │
      │                               ├─ 캐시 조회
      │                               └─ 선택적 deep review → MR 코멘트 게시
      │
      ├─ @aider 멘션 ──► (sync) → aider 자유형 답변 → llm-client 구조화 → MR 코멘트 전송
      │
      └─ MR close/merge ──► 워크스페이스 정리
```

### 디렉토리 구조

```
aider/
├── main.py                  # FastAPI 앱 진입점
├── config.py                # 환경변수 설정 (Pydantic Settings)
├── webhook/
│   ├── handler.py           # Webhook 수신 및 이벤트 분기
│   └── tasks.py             # overview / comment / push-review orchestration
├── git/
│   ├── sync.py              # 저장소 clone/pull
│   └── diff.py              # diff 추출, file diff 파싱, review unit 생성/점수화
├── gitlab/
│   └── client.py            # GitLab API (코멘트, MR 수정)
├── ai/
│   ├── overview.py          # finding 기반 overview / push comment 합성
│   ├── reviewer.py          # review unit 단위 aider deep review
│   ├── comment.py           # 질의응답 생성
│   └── shared/
│       ├── subprocess.py    # Aider CLI subprocess 실행
│       ├── llm_client.py    # OpenAI-compatible llm-client 호출
│       ├── structured.py    # aider raw 응답 → llm-client 구조화 공통 체인
│       └── output.py        # 구조화 파싱 및 GitLab 마크다운 렌더링
├── review/
│   ├── pipeline.py          # review unit 파이프라인 및 캐시 연계
│   └── store.py             # SHA 기반 finding cache 저장/조회
└── workspace/
    └── manager.py           # 작업 디렉토리 관리
```

## 환경변수

### 필수

| 변수 | 설명 | 예시 |
|------|------|------|
| `GITLAB_HOST` | GitLab 서버 주소 (scheme 제외) | `gitlab.example.com` |
| `REMOTE_LLM_BASE_URL` | OpenAI 호환 LLM API Base URL (`/v1` 포함/미포함 모두 허용) | `http://localhost:11434/v1` |
| `REMOTE_LLM_MODEL` | 기본 모델명(하위호환용, 미분리 시 둘 다 사용) | `openai/qwen2.5-coder:32b` |
| `LLM_CLIENT_MODEL` | 구조화용 llm-client 모델명 | `qwen2.5-coder:32b` |
| `AIDER_MODEL` | aider/litellm용 모델명 | `openai/qwen2.5-coder:32b` |
| `PROJECT_TOKEN_{project_id}` | 프로젝트별 GitLab Access Token | `PROJECT_TOKEN_42=glpat-xxxx` |

> 프로젝트 토큰은 여러 개 설정 가능합니다. `project_id`는 GitLab 프로젝트의 숫자 ID입니다.

### 선택

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `REMOTE_LLM_API_KEY` | `dummy` | LLM API 키 (불필요한 경우 생략) |
| `LOG_LEVEL` | `INFO` | 애플리케이션 및 uvicorn 로그 레벨 (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `WORKSPACE_BASE` | `/tmp/aider_workspaces` | 저장소 클론 기본 경로 |
| `AIDER_TIMEOUT` | `600` | Aider 실행 타임아웃 (초) |
| `LLM_TIMEOUT` | `120` | llm-client 구조화 요청 타임아웃 (초) |
| `VALIDATION_COMMAND` | `` | push 리뷰 전 실행할 검증 명령. 비우면 Maven/Gradle/CMake를 자동 감지 |
| `VALIDATION_TIMEOUT` | `180` | 검증 명령 타임아웃 (초) |
| `MAX_DEEP_REVIEW_UNITS` | `12` | 한 번의 리뷰에서 deep review할 최대 unit 수 |
| `MAX_PARALLEL_REVIEWS` | `3` | deep review 시 동시에 실행할 aider 요청 수 |
| `COMMENT_MAX_CONTEXT_FILES` | `3` | comment 응답 시 함께 넘길 최대 파일 수 |
| `COMMENT_EXCLUDE_EXTENSIONS` | `.sh,.yml,.yaml,.json,.toml,.ini,.conf,.env` | comment 응답 컨텍스트에서 제외할 확장자 목록 |
| `DIFF_IGNORE_PATTERNS` | `` | 추가 제외 파일 패턴 (쉼표 구분, glob) |
| `DIFF_OMIT_DELETIONS` | `true` | 삭제 전용 hunk 제거 여부 |
| `BOT_USERNAME` | `` | 봇 GitLab 계정명 (설정 시 자기 멘션 무시) |
| `SERVER_HOST` | `0.0.0.0` | 서버 바인딩 주소 |
| `SERVER_PORT` | `8000` | 서버 포트 |

## 설치

```bash
# 1. uv로 의존성 설치 (가상환경 자동 생성)
uv sync

# 2. Aider 별도 설치
uv pip install aider-chat

# 3. 환경변수 설정
cp .env.example .env
# .env 파일 편집
```

> `uv`가 없다면: `curl -LsSf https://astral.sh/uv/install.sh | sh`

## 실행

```bash
# 포그라운드
uv run python main.py

# 백그라운드 (터미널 종료 후에도 유지)
nohup uv run python main.py > aider_bot.log 2>&1 &
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
- 전체 diff를 파일 단위 `review unit`으로 정규화
- 파일 경로/변경량/키워드 기반으로 위험도 점수 계산
- 위험도가 높은 unit만 aider로 deep review
- review 결과를 SHA 기반 cache에 저장
- MR 제목이 AI가 생성한 한국어 제목으로 교체
- MR 설명이 변경 개요, 파일별 변경 내용, 리뷰 포인트를 포함한 보고서로 교체

### Push 코드리뷰

MR이 열린 상태에서 새 커밋을 push하면 자동으로 증분 리뷰가 수행됩니다:
- 이전 커밋(`oldrev`)부터 `HEAD`까지의 변경분만 분석
- 증분 diff를 review unit으로 분해하고 cache 재사용
- 위험도가 높은 unit만 다시 deep review
- 분석 결과가 MR 코멘트로 게시됨

### @aider 멘션

MR 코멘트에서 `@aider`를 멘션하면 코드 기반으로 답변합니다.

```
@aider 이 코드에서 메모리 누수 가능성이 있는 부분을 알려줘
@aider handle_client 함수의 에러 처리가 적절한지 검토해줘
```

## 다중 언어 지원

diff의 파일 확장자에서 주요 언어를 자동 감지하여 프롬프트 페르소나를 동적으로 적용합니다.

| 지원 언어 | 확장자 |
|-----------|--------|
| C++ | `.cpp`, `.cc`, `.cxx`, `.c`, `.h`, `.hpp` |
| Java | `.java` |
| Python | `.py` |
| Go | `.go` |
| TypeScript | `.ts`, `.tsx` |
| JavaScript | `.js`, `.jsx` |
| Rust | `.rs` |
| Kotlin | `.kt`, `.kts` |
| C# | `.cs` |
| Ruby | `.rb` |
| PHP | `.php` |
| Swift | `.swift` |
| Scala | `.scala` |
| Bash | `.sh` |

- 혼합 언어 MR: 파일 수 기준으로 가장 많이 등장하는 언어를 선택
- 알 수 없는 언어: 언어 비특정 페르소나("백엔드 전문가") 적용
- `@aider` comment 응답 시: AI가 YAML `language` 필드를 직접 판단해 코드 블록 구문 강조 적용

## 현재 리뷰 설계

현재 MR 리뷰 경로는 문자 수 기반 chunk 대신 `review unit + selective deep review` 방식으로 동작합니다.

1. **Diff 정규화**: raw diff를 파일 단위 `ReviewUnit`으로 변환
2. **위험도 점수화**: 변경량, 파일명, SQL/보안/동시성 키워드로 risk score 계산
3. **선택적 Deep Review**: 위험도 높은 unit만 aider에 전달해 자유형 리뷰 생성
4. **llm-client 구조화**: aider 자유형 응답을 별도 llm-client가 태그 기반 structured output으로 변환
5. **Finding Cache**: `source_sha` 기준으로 unit별 finding 저장
6. **합성**: 구조화된 finding을 기반으로 MR overview 또는 push comment 생성

### Risk Score 계산 규칙

현재 `risk_score`는 [git/diff.py](/home/bong/Desktop/work/ai_agent/aider/git/diff.py)에 구현된 규칙 기반 점수입니다.

- 기본 점수: `added_lines + deleted_lines`
- 기본 점수 상한: `40`
- 보안 관련 파일명 포함 시 `+30`
  - `auth`, `permission`, `security`, `login`, `token`
- DB/트랜잭션 관련 키워드 포함 시 `+20`
  - `transaction`, `rollback`, `commit`, `select`, `update`, `delete from`, `insert into`
- 동시성 관련 키워드 포함 시 `+20`
  - `thread`, `lock`, `mutex`, `asyncio`, `await`, `concurrent`, `race`
- API/핸들러 성격 파일명 포함 시 `+15`
  - `api`, `controller`, `handler`, `router`, `endpoint`
- 운영/설정 성격 파일명 포함 시 `+10`
  - `config`, `settings`, `deployment`, `docker`, `k8s`, `helm`
- 추가된 코드에 `TODO`, `FIXME`, `XXX`가 있으면 `+10`
- 테스트 파일이면 `-20`
  - 최소 점수는 `5` 아래로 내려가지 않음
- 최종 점수 상한: `100`

즉, 현재 점수는 "변경량 + 도메인 위험 신호"를 합산하는 방식입니다. 보안, DB, 동시성, API 진입점, 운영 설정 파일은 더 높은 우선순위로 deep review 대상이 됩니다.

### Deep Review 대상 선정

- 현재 기준 점수 `20` 이상인 unit만 deep review 후보가 됩니다.
- 한 번의 리뷰 실행에서 deep review하는 unit 수는 기본값 `12개`입니다.
- deep review는 기본값 `3개` 동시성으로 병렬 실행됩니다.
- 관련 설정은 `MAX_DEEP_REVIEW_UNITS`, `MAX_PARALLEL_REVIEWS` 환경변수로 조정할 수 있습니다.

### Review Unit 예시

`ReviewUnit`에는 다음 정보가 포함됩니다.

- `unit_id`: 캐시 키로 사용할 고유 ID
- `path`: 변경 파일 경로
- `change_type`: `added` / `modified` / `renamed` / `deleted`
- `diff`: 해당 파일의 diff 본문
- `added_lines`, `deleted_lines`
- `risk_score`: deep review 우선순위 점수
- `tags`: `security`, `database`, `concurrency`, `api`, `ops`, `test` 등
- `related_paths`: 같은 디렉토리 또는 테스트 규칙으로 추출한 연관 파일 목록

### Finding Cache

- 저장 위치: `<workspace>/.review_cache/<source_sha>.json`
- 목적: 동일 SHA에 대해 이미 검토한 unit 결과 재사용
- 현재 범위: MR open / push review 파이프라인

## 이번 리팩터링 반영 사항

- 문자 수 기반 chunk 중심 overview 흐름을 제거하고 file-based `review unit` 구조로 변경
- `git/diff.py`에 `FileDiff`, `ReviewUnit`, risk scoring, 관련 파일 추출 로직 추가
- `review/pipeline.py` 신설로 review orchestration과 cache 처리 분리
- `ai/reviewer.py` 신설로 unit 단위 aider prompt 및 structured finding 파싱 추가
- `ai/overview.py`를 chunk reduce 용도에서 finding synthesis 용도로 재구성
- MR open / push review가 SHA 기반 cache를 활용하도록 변경
- aider 호출 시 review 대상 파일과 연관 파일을 `--file`로 명시하여 컨텍스트를 제한

## TODO

- `@aider` comment 경로도 finding cache를 우선 조회하도록 통합
- review unit을 파일 단위에서 hunk 또는 symbol 단위로 세분화할지 검토
- `related_paths`를 현재 규칙 기반에서 import/include graph 기반으로 고도화
- risk scoring 규칙을 설정 파일 또는 정책 파일로 외부화
- cache invalidation 정책을 보강해서 force-push, rebase, rename 시 정합성 검증
- deep review 최소 점수(`_MIN_REVIEW_SCORE`)를 환경변수화
- unit review 결과 스키마에 `evidence_lines`, `needs_followup` 같은 필드 추가
- end-to-end 테스트 추가
- 대규모 MR에서 처리 시간, cache hit ratio, finding 수를 남기는 메트릭/로그 보강

## 주의사항

- GitLab Access Token에는 `api` 스코프가 필요합니다.
- 봇 계정의 무한 멘션 루프 방지를 위해 `BOT_USERNAME` 설정을 권장합니다.
- LLM 응답 품질은 모델 성능에 따라 달라집니다. 현재는 전체 diff를 한 번에 넘기지 않고 high-risk unit 위주로 deep review 합니다.
- 현재 구현은 review unit 단위를 파일 기준으로 생성합니다. 한 파일 내 여러 의미 단위를 아직 분리하지는 않습니다.
- Aider는 repo map을 활용하되, 현재 구현에서는 review 대상 파일과 연관 파일을 `--file` 인자로 함께 전달합니다.
