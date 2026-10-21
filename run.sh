#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="./.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "가상환경이 없습니다. python3 -m venv .venv 후 .venv/bin/pip install -r requirements.txt 를 실행하세요."
  exit 1
fi
"$PYTHON" - <<'PY'
from dotenv import load_dotenv
import os
load_dotenv()
if os.getenv('SERVICE_MODE', 'trendweave') == 'trendweave':
    missing = [k for k in ('GOOGLE_API_KEY','TAVILY_API_KEY') if not os.getenv(k, '').strip()]
    if missing:
        raise SystemExit('.env에 API 키를 설정하세요: ' + ', '.join(missing))
PY
if lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1 || lsof -nP -iTCP:8600 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "8100 또는 8600 포트를 사용 중입니다. 기존 서버를 종료하세요."
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
"$PYTHON" server.py &
API_PID=$!
for _ in {1..60}; do
  if ! kill -0 "$API_PID" 2>/dev/null; then break; fi
  if curl -fsS --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then break; fi
  sleep 0.5
done
if ! curl -fsS --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then
  echo "백엔드 실행에 실패했습니다. 오류 로그를 확인하세요."
  exit 1
fi
"$PYTHON" web_demo_server.py &
WEB_PID=$!
echo "TrendWeave: http://localhost:8600 · 종료: Control + C"
wait "$WEB_PID"
