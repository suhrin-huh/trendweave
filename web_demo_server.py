"""TrendWeave UI and same-origin API proxy (http://localhost:8600).

The backend runs on 127.0.0.1:8100. Static Vue assets are served locally.
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
        try:
            r = await client.get(f"{BACKEND}/health")
        except httpx.HTTPError:
            return JSONResponse({"detail": "분석 서버에 연결할 수 없습니다."}, status_code=502)
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
        try:
            upstream = await client.request(req.method, f"{BACKEND}{req.url.path}",
                                            content=await req.body(),
                                            headers={"content-type": "application/json"})
        except httpx.HTTPError:
            return JSONResponse({"detail": "분석 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."}, status_code=502)
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


