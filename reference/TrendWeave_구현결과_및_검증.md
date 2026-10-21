# TrendWeave 1차 구현 결과 및 검증

검증일: 2026-10-21

## 구현 범위

`TrendWeave_웹서비스_구현계획.md`의 1차 범위를 구현했다. 현재 웹 화면은 UNIQLO 대상 상품기획 서비스이며, 예시 질문 버튼 또는 자유 입력을 통해 런웨이·컬러·시장·브랜드를 모두 조사한다.

- 노트북의 요청 프로필과 검색 계획, Send 기반 병렬 조사, 문서 평가, 최대 3회 검색, 근거 연결 Signal 추출을 이식했다.
- 상품기획 방향 3개, 규칙 검사 및 LLM Critic, 최대 2회 재작성과 미해결 검토 표시를 유지했다.
- 생성은 POST로 수행하고, 작업 ID를 기준으로 조회·SSE 구독·PDF 다운로드를 제공한다.
- SQLite에 작업과 순번 있는 이벤트를 저장한다. 페이지 새로고침은 같은 작업에 재연결하고 마지막 완료 보고서도 복원한다.
- 보고서 요약, 분야별 자료 충분도, 전체 Markdown 미리보기와 PDF 다운로드를 제공한다.
- PDF는 저장된 최종 Markdown을 변환하므로 분석을 재실행하지 않는다. 로컬 Noto 한국어 글꼴, 표·참고 링크·전체 Trace를 포함한다.
- 모델과 검색 설정이 없으면 분석 준비 오류를 표시한다. 실제 검색을 수행하는 것처럼 보이는 시연용 가짜 분석 모드는 추가하지 않았다.

질문별 선택적 분석, 동적 보고서 형식, 다른 브랜드, 이전 대화에 대한 후속 분석, 사용자별 보고서 목록은 구현하지 않았다.

## 주요 파일

| 파일/경로 | 역할 |
|---|---|
| `agent/trendweave/schemas.py`, `state.py`, `config.py` | 노트북 데이터 구조·Reducer·축별 기준 및 루프 상한 |
| `agent/trendweave/engine.py` | 원본 프롬프트·규칙·Research 서브그래프·전체 그래프 |
| `agent/trendweave/deps.py`, `search.py` | Gemini 모델·공용 속도 제한기 초기화, timeout을 가진 Tavily 연결 |
| `services/report_store.py` | 작업·결과·이벤트 저장 및 재시작 정리 |
| `services/report_service.py` | 대기 개수 제한, 백그라운드 그래프 실행과 결과 직렬화 |
| `services/report_routes.py` | 생성·조회·SSE·PDF API |
| `services/pdf_service.py`, `templates/report_pdf.html` | HTML 정제, 한국어 PDF 변환 및 인쇄 스타일 |
| `server.py`, `web_demo_server.py` | API 연결 및 바이너리·SSE 동일 출처 프록시 |
| `web_chat.html`, `static/trendweave.*` | 채팅형 상품기획 화면·예시 버튼·보고서 모달 |
| `tests/`, `scripts/verify_live.py`, `scripts/verify_browser.py` | 자동 테스트와 실제 API·브라우저 검증 |

기존 장애 대응 코드와 MCP 자원은 남겨두었다. 기본 `SERVICE_MODE=trendweave`에서는 기존 MCP 서버 기동과 독립적으로 패션 서비스가 시작된다. 기존 실습 API는 `SERVICE_MODE=incident`에서 사용할 수 있다.

## 검증 결과

### 1. 자동 테스트

실행:

```bash
.venv/bin/python -m pytest -q
```

결과: **14 passed**.

- 4축 결과 병합과 Strategy 단일 실행, 요청별 검색 계획 반영
- 빈 검색 결과의 시도 상한 및 전체 자료 부재 처리
- 근거 ID 정리와 존재하지 않는 근거 검출
- Color Signal이 비었을 때 불필요한 컬러 오류 방지
- Critic 실패 대체 처리와 재작성 횟수 상한
- 시즌 불일치 평가 및 Market 시즌 규칙
- Tavily HTTP timeout과 검색 옵션 전달
- 생성·조회·입력 검증·설정 오류·SSE 재생
- 작업별 이벤트 분리, 서버 재시작 시 미완료 정리와 완료 결과 유지
- 작업 대기 한도 및 실패 처리
- Markdown 스크립트·위험 링크 제거
- PDF 생성 실패 시 보고서 완료 상태 유지
- Chromium PDF 다운로드, 한국어 텍스트와 글꼴 자원·전체 섹션 확인

Python compileall, JavaScript 구문 확인, `pip check`도 통과했다.

### 2. 실제 Gemini·Tavily 분석

`verify_live.py`에서 설정된 실제 API 키와 노트북의 모델 ID를 사용했다. 다음 입력으로 실제 외부 검색과 분석을 완료했다.

```text
시즌: 2027 S/S
브랜드: UNIQLO
요청: 한국의 20-30대 고객이 일상에서 활용하기 쉬운 상품 중심으로 기획해줘.
```

결과:

- 4축 모두 결과 생성, 자료 충분도 `높음`
- 상품기획 방향 3개
- Critic 피드백 후 1회 재작성
- 규칙 및 LLM 검토 통과
- 최종 Markdown·구조화 결과 저장

이 결과는 해당 실행의 관찰값이다. 매 실행의 자료 품질이나 Critic 통과를 보장하는 수치는 아니다.

### 3. 실제 브라우저 종단 간 검증

`verify_browser.py`는 실제 두 서버를 시작하고 Chromium으로 다음 흐름을 수행했다.

```text
웹 접속 → 예시 질문 버튼 클릭 → 202 작업 생성
→ 생성 중 페이지 새로고침 → 기존 작업 ID로 복원
→ 4축 실제 분석 완료 → 전체 보고서 모달
→ PDF 다운로드 → PDF 텍스트 확인
→ 모바일 화면 및 모달 확인 → 서버 종료
```

결과:

- 실제 작업 완료, 상품기획 방향 3개
- 4축 자료 충분도 `높음`, Critic 재작성 2회 후 LLM 검토 통과
- **7페이지 PDF** 다운로드 및 한글·References·Agent Trace 포함 확인
- 데스크톱 1440×1000, 모바일 390×844에서 문서 가로 넘침 없음
- 브라우저 JavaScript 오류 0건
- 최종 화면 수정 후 `--reuse-last` 옵션으로 완료된 실제 보고서 복원과 PDF 다운로드를 추가 확인했다. 이 재검증에서는 외부 분석을 다시 실행하지 않았다.
- 데스크톱·모바일 화면, 보고서 미리보기 및 PDF 첫 페이지를 직접 확인했다.

개발 환경의 로컬 산출물:

- `../.artifacts/browser-report.pdf`: 실제 화면에서 다운로드한 PDF
- `../.artifacts/browser-report.md`: 같은 보고서의 최종 Markdown
- `../.artifacts/browser-verification.json`: 브라우저 검증 요약
- `../.artifacts/desktop.png`, `mobile.png`: 최초 화면
- `../.artifacts/completed.png`, `report-preview.png`, `mobile-report.png`: 완료·미리보기 화면

산출물과 DB·로그는 Git에서 제외했다. 새 환경에서는 검증 스크립트로 다시 생성할 수 있다.

## 발견한 문제와 조치

| 문제 | 원인 | 조치 |
|---|---|---|
| Research의 세부 단계가 상위 SSE에 보이지 않음 | 중첩 그래프의 custom stream이 자동 전달되지 않음 | 서브그래프를 custom/values 스트림으로 소비하고 부모 writer로 이벤트를 명시적으로 전달 |
| 외부 검색 응답이 무기한 지연될 가능성 | 설치한 langchain-tavily의 동기 HTTP 호출에 timeout이 없음 | 동일 검색 입력·결과 계약을 가진 httpx 어댑터에 timeout 적용 |
| 재연결 시 분석 중복 실행 가능성 | 생성 작업을 GET SSE 구독과 묶으면 자동 재연결 때 재실행될 수 있음 | POST 작업 생성과 GET 이벤트 구독을 분리하고 이벤트 ID로 재생 |
| 오래된 비동기 상태 응답이 새 작업 연결을 닫을 가능성 | 이전 작업 polling 응답이 다음 작업 시작 뒤 도착할 수 있음 | 활성 작업을 비교하여 연결 해제 범위를 제한 |
| 생성 중/완료 후 새로고침 시 결과 소실 | 화면의 메모리 상태만 사용 | 현재 탭의 마지막 작업 ID와 요청을 세션 저장소에 저장하고 API로 복원 |
| Chromium 설치·실행 및 외부 연결 검증 제한 | 개발 샌드박스의 파일·네트워크·프로세스 제한 | 승인된 실행 환경에서 브라우저 설치와 실연동 검증 완료 |

## 실행과 현재 한계

`.env`의 `GOOGLE_API_KEY`, `TAVILY_API_KEY`를 설정하고 아래 명령으로 실행한다.

```bash
./run.sh
```

접속 주소는 `http://localhost:8600`이다. 설치·설정·검증 명령과 API 설명은 루트 README에 정리했다.

한 프로세스의 로컬 데모를 기준으로 한다. 기본값은 동시 분석 1개, 실행·대기 합계 3개이며 미완료 작업 자동 재개는 지원하지 않는다. 보고서는 공개 검색 결과의 일부 텍스트를 근거로 하며 자료 충분도가 사실의 정확도를 보증하지 않는다. 재검색·재작성 한도와 데이터 부족은 보고서에 남긴다.

## 작업 브랜치와 커밋 구분

브랜치: `feature/trendweave-reports`

1. `13e6c5a`: 노트북 기반 4축 분석·검토 그래프 이식
2. `a44ebf3`: 보고서 작업 저장, 조회, 재연결 가능한 SSE API
3. `f7eac0a`: 예시 질문 UI, 보고서 미리보기, 한국어 PDF 다운로드
4. `5ccffbf`: 실제 통합 검증 기반 안정화 및 그래프·API·PDF·브라우저 검증 코드
5. 실행 안내·승인된 구현 계획·구현 결과 문서 정리
