"""web_demo_server.py — all_total_vue 에이전트와 브라우저 채팅창으로 대화해보는 데모.

web_chat.html이 바닐라 JS 대신 Vue 3(CDN 글로벌 빌드, 별도 빌드 단계 없음)로 되어
있다는 점만 all_total과 다르다. server.py(8100, 기존 통합 테스트 서버)는 전혀
건드리지 않는다. 이 스크립트는
- web_chat.html 한 장을 같은 출처(origin)로 띄우고
- /chat/stream 요청을 그대로 8100으로 중계(프록시)해서

브라우저의 CORS 제약 없이 실제 에이전트와 대화할 수 있게 해주는 아주 얇은 레이어다.
server.py는 평소처럼(fake/ollama/openai 아무 백엔드로나) 8100에 미리 띄워 놓아야 한다.

실행:
    python3 web_demo_server.py        # http://localhost:8600 에서 채팅 화면 오픈
"""
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response

BACKEND = "http://127.0.0.1:8100"
HERE = Path(__file__).parent

app = FastAPI()
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(HERE / "web_chat.html")


@app.get("/health")
async def proxy_health():
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND}/health")
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/chat")
async def proxy_chat(req: Request):
    body = await req.body()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{BACKEND}/chat", content=body,
                              headers={"content-type": "application/json"})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/chat/stream")
async def proxy_stream(message: str, thread_id: str = "web-demo"):
    async def relay():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", f"{BACKEND}/chat/stream",
                params={"message": message, "thread_id": thread_id},
            ) as upstream:
                async for chunk in upstream.aiter_bytes():
                    yield chunk

    return StreamingResponse(relay(), media_type="text/event-stream")


# Report API proxy: retain error status and PDF attachment headers.
@app.api_route("/reports", methods=["POST"])
@app.api_route("/reports/{report_id}", methods=["GET"])
@app.api_route("/reports/{report_id}/pdf", methods=["GET"])
async def proxy_report(req: Request, report_id: str = ""):
    async with httpx.AsyncClient(timeout=120.0) as client:
        upstream = await client.request(req.method, f"{BACKEND}{req.url.path}",
                                        content=await req.body(),
                                        headers={"content-type": "application/json"})
    headers = {key: upstream.headers[key] for key in
               ("content-type", "content-disposition") if key in upstream.headers}
    return Response(upstream.content, status_code=upstream.status_code, headers=headers)


@app.get("/reports/{report_id}/events")
async def proxy_report_events(report_id: str, req: Request):
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
    headers = {}
    if req.headers.get("last-event-id"):
        headers["Last-Event-ID"] = req.headers["last-event-id"]
    try:
        upstream = await client.send(client.build_request(
            "GET", f"{BACKEND}/reports/{report_id}/events", params=req.query_params,
            headers=headers), stream=True)
    except Exception:
        await client.aclose()
        return JSONResponse({"detail": "분석 서버에 연결할 수 없습니다."}, status_code=502)
    if upstream.status_code != 200:
        content = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        return Response(content, status_code=upstream.status_code, media_type="application/json")

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
    return StreamingResponse(relay(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

if __name__ == "__main__":
    print("브라우저에서 http://localhost:8600 을 열어보세요 (server.py가 8100에 떠 있어야 합니다)")
    uvicorn.run(app, host="0.0.0.0", port=8600)


