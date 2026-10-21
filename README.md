# TrendWeave · 패션 트렌드 상품기획 에이전트

기존 Vue 채팅·FastAPI 서비스를 바탕으로 `reference/AI_Agent_설계_trendweave.ipynb`를 웹서비스로 이식했습니다. ZARA를 대상으로 런웨이·컬러·시장·브랜드를 모두 조사하고 상품기획 방향 3개를 제안합니다.

## 실행

Python 3.11 이상을 사용하세요. 최초 설치:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cp .env.example .env
```

이미 `.env`가 있다면 덮어쓰지 말고 필요한 항목을 추가하세요.

```dotenv
SERVICE_MODE=trendweave
GOOGLE_API_KEY=본인의_Gemini_API_키
TAVILY_API_KEY=본인의_Tavily_API_키
TREND_MODEL=gemini-3.1-flash-lite
TREND_REQUESTS_PER_SECOND=0.25
TREND_MAX_WORKERS=1
TREND_SEARCH_TIMEOUT=30
```

모델 ID는 노트북 기준이며 설정으로 교체할 수 있습니다. 실제 서비스 실행에는 Gemini와 Tavily의 유효한 키가 모두 필요합니다. 호출 한도는 계정에 맞게 조정하세요. 분석 및 검증 스크립트는 실제 외부 API를 호출합니다.

```bash
./run.sh
```

브라우저에서 <http://localhost:8600>을 엽니다. API는 `127.0.0.1:8100`에서 실행합니다. 종료는 `Control + C`입니다.

## 사용 방법

1. 분석 시즌을 선택합니다. 기본값은 `2027 S/S`, 브랜드는 ZARA입니다.
2. **예시 질문으로 보고서 생성**을 클릭하면 바로 분석이 시작됩니다. 자유 입력 요청도 같은 4축 분석으로 실행됩니다.
3. 진행 기록에서 분야별 검색·재검색·추출, 전략 작성·검토를 확인합니다. 처리 시간은 약 2~4분 이상이며 호출 한도에 따라 늘어날 수 있습니다.
4. 완료 후 요약과 분야별 자료 충분도를 확인하고 **전체 보고서 보기**를 엽니다.
5. **PDF 다운로드**로 동일한 최종 보고서를 저장합니다. 한국어 글꼴, 전체 검토 기록과 참고 자료가 포함됩니다.

새로고침하면 이전 메시지·보고서 카드·입력 내용이 초기화되고 새로운 대화로 시작합니다. 진행 중 새로고침해도 서버의 분석은 계속되지만 화면에서는 다시 구독하지 않습니다. 저장된 보고서는 삭제하지 않으며, 새 요청은 독립적인 분석입니다.

## 동작 구조

```text
Vue 채팅 → 8600 동일 출처 프록시 → 8100 FastAPI
          POST /reports → 작업 ID → 백그라운드 LangGraph
          GET /reports/{id}/events → SSE 진행 상태
          GET /reports/{id} → 저장된 결과와 정제된 HTML
          GET /reports/{id}/pdf → 저장 Markdown → HTML → Chromium PDF

정규화 → Planner → Send ×4 Research → Strategy ↔ Critic → Report Builder
                   검색 → Grade → Rewrite / Extract
```

`agent/trendweave/`는 노트북의 스키마·State·축 설정·그래프를 포함합니다. `engine.py`는 원본 프롬프트와 규칙을 유지하며, `deps.py`에서 시작 시 모델을 주입합니다. `search.py`는 Tavily의 동기 호출에 명시적인 HTTP timeout을 적용합니다.

`services/`는 SQLite 작업·이벤트 저장, 제한된 작업 스레드 실행, API, Markdown 정제·PDF 변환을 담당합니다. 기본 DB는 `reports.db`이고 `REPORT_DB_PATH`로 바꿀 수 있습니다. 서버 재시작 시 미완료 작업은 실패로 정리하고 완료 결과는 유지합니다. `static/`의 Vue와 PDF 글꼴은 로컬 자원이며 라이선스 파일을 함께 포함했습니다.

### 생성 요청 예

```bash
curl -X POST http://127.0.0.1:8100/reports \
  -H 'Content-Type: application/json' \
  -d '{"season":"2027 S/S","planning_request":"한국 20-30대 고객의 일상 상품을 기획해줘.","thread_id":"demo"}'
```

반환된 `report_id`로 조회합니다. 생성은 `202`, 키 설정 미완료는 `503`, 입력 오류는 `422`, 대기 한도 초과는 `429`입니다. 존재하지 않는 보고서는 `404`, 완료 전 PDF 요청은 `409`입니다.

## 검증

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -m 'not browser' -q
.venv/bin/python -m pytest -m browser -q
```

`not browser` 테스트는 실제 외부 API 없이 그래프·규칙·작업 저장·API·오류 처리를 검증합니다. `browser` 테스트는 설치된 Chromium으로 한국어 PDF를 생성하고 내용을 확인합니다.

실제 API 통합 검증은 필요할 때 별도로 실행합니다.

```bash
.venv/bin/python scripts/verify_live.py
.venv/bin/python scripts/verify_browser.py
```

`verify_live.py`는 실제 4축 분석을 수행합니다. `verify_browser.py`는 8100·8600 서버를 실행하고 실제 화면에서 예시 질문을 클릭한 뒤 보고서 보기·PDF 다운로드·모바일 레이아웃·새로고침 대화 초기화까지 검증하고 서버를 종료합니다. 두 포트가 비어 있어야 합니다. PDF·스크린샷·결과는 Git에서 제외된 `.artifacts/`에 저장됩니다.

## 구현 범위와 한계

- 1차 범위인 ZARA 고정 4축 조사, 요청 반영, 재검색, 전략 3개, 검토·재작성, 웹 보고서, PDF 다운로드를 구현했습니다.
- 검색 총 3회/축, 추출 최대 6개 문서/축, 전략 재작성 최대 2회로 제한합니다. 자료 부족 및 검토 한도 도달은 보고서에 표시합니다. 모든 축의 자료 수집이 실패하면 작업을 실패 처리합니다.
- 한 프로세스의 로컬 데모용입니다. 기본 동시 분석은 1개이며 실행·대기 작업은 합계 3개까지 허용합니다. 완료 DB는 유지하지만 서버 재시작 후 미완료 분석을 자동 재개하지 않습니다.
- 문서 검색 결과의 텍스트 일부를 사용하며 신뢰도는 자료 충분도를 뜻합니다. 판매·재고·마진을 분석한 결과가 아닙니다.
- 질문별 선택적 분석, 동적 보고서 형식, 다른 브랜드, 대화 문맥 기반 후속 분석, 계정별 보고서 목록은 구현하지 않았습니다.

## 참고한 공식 문서

- [LangChain Google GenAI 구조화 출력](https://reference.langchain.com/python/langchain-google-genai/chat_models/ChatGoogleGenerativeAI/with_structured_output)
- [Playwright PDF API](https://playwright.dev/python/docs/api/class-page#page-pdf)
- [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)

---

# 기존 베이스코드 문서 (운영 장애 대응 실습)

아래 내용은 기존 실습용 코드의 설명입니다. 기본 웹 화면은 TrendWeave이며, 기존 장애 대응 API를 사용할 때는 `SERVICE_MODE=incident`와 기존 `LLM_BACKEND` 설정을 사용하세요.

# AI Agent 종합실습 — 실행 및 과제 가이드 (macOS)

이 실습은 **아래 두 과제 중 하나를 선택**하여 진행합니다.

- **과제 1. 기존 Base AI Agent 개선:** 제공된 `all_total_vue_student` 프로젝트를 분석하고 기능을 개선하거나 새 기능을 추가합니다.
- **과제 2. 신규 AI 서비스 개발:** 자유롭게 주제를 정해 AI 서비스를 처음부터 기획·설계·구현합니다. **제공 프로젝트를 반드시 사용할 필요는 없습니다.**

> 이 저장소는 **과제 1의 시작 코드**입니다. 과제 2 가이드라인은 아래에 별도로 정리했습니다.

## 1. 기존 프로젝트 소개

`all_total_vue_student`는 서버 장애 상황을 확인하고 대응 방안을 제안하는 **운영 인시던트 대응 AI Agent** 예제입니다.

| 구성 | 역할 |
| --- | --- |
| Vue 3 (`web_chat.html`) | 질문 입력, 답변 및 처리 단계 표시 |
| FastAPI (`server.py`) | 채팅 요청 처리, 상태 확인, SSE 응답 전송 |
| LangGraph (`agent/graph.py`) | 요청 분류 → 조사 계획 → 정보 검색 → 분석 → 답변 검증 |
| MCP (`mcp_server.py`, `agent/mcp_client.py`) | 모니터링 데이터·장애 대응 매뉴얼 조회 |
| SQLite (`agent/session_store.py`) | 대화 세션 저장 |
| `fake` / `openai` / `ollama` | 테스트용 응답 / OpenAI API / 로컬 LLM 중 선택 |

프로젝트 화면은 **Vue CDN 방식**으로 제공되므로 **Node.js, npm 설치 및 Vue 빌드가 필요 없습니다.** 브라우저에서 Vue CDN을 불러오려면 인터넷 연결이 필요합니다.

## 2. Mac에서 설치하고 실행하기

### 2-1. 최초 설치

Mac 터미널에서 프로젝트 압축을 푼 폴더로 이동합니다. 아래는 `Downloads`에 압축을 푼 경우입니다.

```bash
cd ~/Downloads/all_total_vue_student
python3 --version
python3 -m venv .venv
./.venv/bin/python3 -m pip install -r requirements.txt
```

Python **3.11 이상**을 사용하세요. 가상환경 생성 및 패키지 설치는 최초 한 번만 하면 됩니다.

### 2-2. 실행 모드 선택

**① 기본 테스트 모드 (API 키 필요 없음)**

```bash
bash run.sh
```

`fake`는 정해진 테스트 응답을 반환하는 모드입니다. **실제 LLM 성능 평가용은 아닙니다.** 화면·요청 흐름·코드 수정 후 기본 동작을 확인할 때 사용합니다.

**② OpenAI API 모드 (실제 LLM 사용)**

```bash
cp .env.example .env
nano .env
```

`.env`의 `OPENAI_API_KEY=` 항목에 본인의 API 키를 입력합니다. `nano` 저장: `Control + O` → `Enter` → `Control + X`.

```bash
LLM_BACKEND=openai bash run.sh
```

실제 API 호출에 따른 **사용 요금이 발생할 수 있습니다.** `.env` 파일과 API 키는 GitHub 등에 올리지 마세요.

**③ Ollama 모드 (Mac 로컬 LLM 사용)**

Mac에 [Ollama](https://ollama.com/download/mac)를 설치한 뒤 터미널에서 필요한 모델을 받습니다.

```bash
ollama pull qwen3:0.6b
ollama pull qwen3:8b
LLM_BACKEND=ollama bash run.sh
```

Ollama 앱을 먼저 실행해야 합니다. 동작하지 않는다면 별도 터미널에서 `ollama serve`를 실행하세요. 특히 `qwen3:8b`는 메모리 사용량이 크므로 Mac 사양을 확인하세요.

### 2-3. 접속 및 종료

- **웹 화면:** http://localhost:8600
- **백엔드 상태:** http://localhost:8100/health
- **API 문서:** http://localhost:8100/docs
- **종료:** 실행 중인 터미널에서 `Control + C`

`run.sh` 하나가 FastAPI 서버(8100)와 웹 서버(8600)를 함께 실행합니다. 다음 실행부터는 원하는 모드의 `bash run.sh` 명령만 입력하면 됩니다.

## 3. 웹 화면 기본 사용 방법

1. 브라우저에서 http://localhost:8600 을 엽니다.
2. 하단 입력창에 질문을 작성하고 **보내기**를 누릅니다.
3. 질문에 따라 계획 수립, 모니터링 조회, 대응 매뉴얼 검색, 답변 검증 등의 처리 과정과 최종 답변이 표시됩니다.
4. 일부 응답에서는 **「LLM에 전달된 최종 프롬프트 보기」**를 펼쳐 입력 내용을 확인할 수 있습니다.
5. 동일 화면에서 연속 질문하면 동일 대화 세션을 사용합니다. **브라우저를 새로 열면 세션 ID가 바뀌므로** 이전 대화가 자동으로 이어지지는 않습니다.

**체험용 질문 예시**

```text
안녕하세요. 무슨 기능을 제공하나요?
auth-service 로그인 응답이 느려졌어요. 원인을 확인해주세요.
web-01 CPU 사용률이 급증했어요. 대응 방법을 알려주세요.
payments-service와 cache-service에 문제가 있어요. 함께 분석해주세요.
```

**백엔드만 확인할 때** (서버 실행 중 다른 터미널에서):

```bash
curl http://localhost:8100/health

curl -X POST http://localhost:8100/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"auth-service 응답이 느려요","thread_id":"test-01"}'
```

기본 샘플 데이터는 `data/monitoring.json` 및 `data/runbooks/`에 있습니다.

## 4. 과제 1 — 기존 Base AI Agent 개선

### 목표

제공된 프로젝트의 구조를 이해하고 **문제점이나 개선 필요 사항을 찾은 뒤**, 실제 코드를 변경하여 개선 전후 동작을 검증합니다.

### 개선 주제 예시

| 개선 주제 | 확인하거나 수정할 위치 예시 |
| --- | --- |
| RAG 검색 기능 개선 | `mcp_server.py`, `agent/mcp_client.py`, `data/runbooks/` |
| LangGraph 노드 모듈화 | `agent/graph.py` |
| Multi-Agent 구조 적용 | `agent/graph.py`, `agent/deps.py` |
| Human-in-the-loop 승인 기능 | `agent/graph.py`, `server.py`, `web_chat.html` |
| 대화 메모리 및 Context 관리 | `agent/session_store.py`, `server.py` |
| LLM-as-a-Judge 평가 기능 | 새로운 평가 모듈 추가, 테스트 케이스 구축 |
| 하드코딩된 로직 개선 | `agent/graph.py`, `agent/fake_llm.py` 등 관련 구현 |
| 샘플 데이터 확장 | `data/monitoring.json`, `data/runbooks/` |

표의 파일 경로는 **시작점 예시**이며, 실제 수정 범위는 선택한 기능에 따라 달라집니다.

### 권장 진행 순서

1. **기본 실행:** `bash run.sh`로 기존 기능을 체험하고 정상 응답을 확인합니다.
2. **코드 분석:** 개선 대상 기능과 관련 파일을 찾아 현재 처리 흐름을 이해합니다.
3. **문제 정의:** 현재 동작의 한계, 실패 사례 또는 추가하려는 기능을 구체적으로 기록합니다.
4. **개선 구현:** 관련 Python 코드·데이터·웹 화면을 필요한 범위만큼 수정합니다.
5. **검증:** 같은 입력을 사용해 **개선 전 / 개선 후** 결과를 비교하고 기존 기능도 재검사합니다.

**검증 예시:** 시스템 이름 인식 개선을 선택했다면, 기존에 제대로 처리하지 못했던 시스템명으로 질문하고 수정 전후에 조회 대상과 최종 응답이 어떻게 달라졌는지 확인합니다. `doc/regression_test_queries.md`를 테스트 출발점으로 사용할 수 있습니다.

> `fake` 모드로 화면과 처리 흐름을 먼저 점검하고, 실제 LLM 판단의 정확성은 OpenAI 또는 Ollama 모드에서 따로 확인하세요.

## 5. 과제 2 — 신규 AI 서비스 개발

### 목표

**자유롭게 주제를 선정**하고, 사용자가 직접 활용할 수 있는 AI 서비스를 새로 개발합니다. 기존 Base Agent를 개선하는 과제 1과 달리 **문제 정의부터 시스템 설계·구현·검증까지 직접 진행**합니다.

### 주제 예시

- **사내 문서 Q&A 서비스:** PDF·문서 업로드 → 검색(RAG) → 근거 포함 답변
- **학습 도우미 AI:** 학습 자료 분석 → 질문 생성 → 정답 피드백
- **고객 문의 응대 AI:** 문의 분류 → 내부 자료 검색 → 답변 작성
- **일정·업무 지원 Agent:** 사용자 요청 해석 → 도구 호출 → 결과 요약
- **코드 리뷰 서비스:** 코드 입력 → 문제점 분석 → 개선안 제안

위 목록은 **예시**이며, 다른 주제를 선택해도 됩니다.

### 기본 구현 가이드라인 (권장)

| 항목 | 기본 가이드 |
| --- | --- |
| 서비스 목적 | 어떤 사용자의 어떤 문제를 해결하는지 명확히 정의 |
| 입력과 출력 | 사용자가 제공하는 입력과 AI가 반환할 결과 정의 |
| AI 기능 | LLM, RAG, Agent, 도구 호출 등 **서비스 목적에 맞는 기능** 선택 |
| 데이터 | 사용할 문서·DB·외부 API·샘플 데이터 준비 및 출처 확인 |
| 백엔드 | Python + FastAPI 등으로 AI 기능을 호출하는 API 구현 가능 |
| 화면 | Vue 등으로 주요 기능을 직접 테스트할 수 있는 UI 구현 가능 |
| 안정성 | 빈 입력, 잘못된 데이터, API 실패 등 기본 예외 처리 |
| 검증 | 정상·오류·경계 사례로 실제 실행 결과 확인 |

**기술 스택은 예시입니다.** 주제에 따라 프론트엔드 없이 API·CLI 중심으로 만들거나, LangGraph/MCP 없이 구현해도 됩니다. 단, **어떤 부분에 AI를 사용했으며 실제로 어떻게 동작하는지** 설명할 수 있어야 합니다.

### 권장 개발 순서

1. **주제 선정:** 서비스명, 대상 사용자, 해결할 문제, 주요 기능 2~3개 작성
2. **요구사항 정리:** 입력·출력·필요 데이터·외부 API·예상 실패 상황 정의
3. **설계:** 화면 흐름과 `사용자 → 서비스 → AI/데이터 → 결과` 처리 과정 정리
4. **최소 기능(MVP) 개발:** 대표 시나리오 하나가 처음부터 끝까지 동작하도록 구현
5. **기능 확장:** 검색, 대화 기록, 결과 저장, 피드백, 평가 등 주제에 맞춰 추가
6. **테스트 및 개선:** 질문/입력별 결과 확인, 실패 원인 수정, 개선 전후 비교

### 검증 예시

문서 Q&A 서비스를 만들었다면 아래와 같이 점검할 수 있습니다.

| 테스트 상황 | 확인할 내용 |
| --- | --- |
| 문서에 답이 있는 질문 | 정확한 답과 관련 근거 제시 여부 |
| 문서에 답이 없는 질문 | 근거 없이 답을 만들어 내지 않는지 |
| 빈 질문 또는 잘못된 파일 | 사용자에게 이해 가능한 오류 메시지 표시 여부 |
| 같은 질문 반복 | 답변 품질과 처리 시간의 일관성 |

> 화면이 뜨는 것만으로 완료하지 말고 **AI 기능의 입력 → 처리 → 결과가 실제로 연결되는지** 확인하세요.

## 6. 공통 결과 정리 가이드

아래 항목을 README나 결과 보고서에 남기는 것을 권장합니다. **공식 제출 형식이나 평가 배점은 별도 과제 안내가 있다면 그 기준을 우선 적용**하세요.

- **프로젝트 소개:** 선택한 과제, 서비스 목적, 주요 기능
- **실행 방법:** Mac 기준 설치 명령어, 환경변수, 실행 및 접속 방법
- **구현 내용:** 사용 기술, 주요 파일, 전체 동작 흐름
- **검증 결과:** 테스트 입력, 예상 결과, 실제 결과, 성공·실패 여부
- **개선 내용:** 과제 1은 기존 코드의 문제점과 변경 전후 비교 / 과제 2는 구현 기능과 해결한 문제
- **시연 자료:** 실제 실행 화면 또는 시연 영상, 필요시 테스트 로그

## 7. 자주 발생하는 실행 오류

| 상황 | 확인할 내용 |
| --- | --- |
| `python3` 명령을 찾을 수 없음 | Mac에 Python 3.11 이상 설치 여부 확인 |
| `ModuleNotFoundError` | `./.venv/bin/python3 -m pip install -r requirements.txt` 재실행 |
| `8100 또는 8600 포트 사용 중` | 해당 포트의 기존 서버 종료 후 재실행 |
| OpenAI 실행 실패 | `.env`의 `OPENAI_API_KEY`, 모델 접근 권한 및 사용 가능 크레딧 확인 |
| Ollama 연결 실패 | Ollama 앱 실행 여부 및 다운로드한 모델 이름 확인 |
| 웹 페이지가 비어 있음 | 인터넷 연결(Vue CDN), 브라우저 개발자 도구 오류 확인 |

더 상세한 내부 동작 설명과 실행 예시는 `doc/README.md`, `doc/manual_openai.md`, `doc/manual_ollma.md`를 참고하세요.
