#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="./.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  echo "가상환경이 없습니다. README.md의 '최초 설치'부터 진행하세요."
  exit 1
fi

MODE="${LLM_BACKEND:-fake}"
case "$MODE" in
  fake|openai|ollama) ;;
  *) echo "지원하지 않는 LLM_BACKEND: $MODE (fake / openai / ollama)"; exit 1 ;;
esac

if [[ "$MODE" == "openai" ]]; then
  "$PYTHON" - <<'CHECK_KEY'
import os
from dotenv import load_dotenv
load_dotenv(".env")
key = os.getenv("OPENAI_API_KEY", "").strip()
if not key or key == "sk-...":
    raise SystemExit("OPENAI_API_KEY가 없습니다. .env에 본인 키를 설정하세요.")
CHECK_KEY
fi

if [[ "$MODE" == "ollama" ]] && ! curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "Ollama 서버가 실행 중이지 않습니다. Ollama 앱을 켜거나 ollama serve를 실행하세요."
  exit 1
fi

if lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1 ||    lsof -nP -iTCP:8600 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "8100 또는 8600 포트를 이미 사용 중입니다. 기존 서버를 먼저 종료하세요."
  exit 1
fi

API_PID=""
WEB_PID=""
cleanup() {
  [[ -z "$WEB_PID" ]] || kill "$WEB_PID" 2>/dev/null || true
  [[ -z "$API_PID" ]] || kill "$API_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

LLM_BACKEND="$MODE" SUPERVISOR_VERSION="${SUPERVISOR_VERSION:-v2}" "$PYTHON" server.py &
API_PID=$!
for _ in {1..60}; do
  if ! kill -0 "$API_PID" 2>/dev/null; then break; fi
  if curl -fsS --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then break; fi
  sleep 0.5
done
if ! curl -fsS --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then
  echo "백엔드 실행에 실패했습니다. 위 오류 로그를 확인하세요."
  exit 1
fi

"$PYTHON" web_demo_server.py &
WEB_PID=$!
echo "실행 모드: $MODE"
echo "웹 채팅: http://localhost:8600"
echo "종료: Control + C"
wait "$WEB_PID"
