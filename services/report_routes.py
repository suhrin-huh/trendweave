import asyncio
import json
import re
from typing import Annotated
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from agent.trendweave.config import DEFAULT_BRAND

router = APIRouter()


class ReportRequest(BaseModel):
    season: str = Field(min_length=4, max_length=20)
    planning_request: str = Field(min_length=1, max_length=4000)
    thread_id: str = Field(default='web', min_length=1, max_length=100)

    @field_validator('planning_request', 'thread_id', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('season')
    @classmethod
    def season_format(cls, value):
        value = value.strip().upper()
        match = re.fullmatch(r'(20\d{2})\s*(S/?S|F/?W)', value)
        if not match:
            raise ValueError('시즌은 2027 S/S 또는 2027 F/W 형식으로 입력하세요.')
        return f'{match[1]} {"S/S" if match[2].startswith("S") else "F/W"}'


def get_report(request, report_id):
    report = request.app.state.report_store.get(report_id)
    if report is None:
        raise HTTPException(404, '보고서를 찾을 수 없습니다.')
    return report


@router.post('/reports', status_code=202)
async def create_report(body: ReportRequest, request: Request):
    if request.app.state.trend_error:
        raise HTTPException(503, request.app.state.trend_error)
    try:
        report_id = request.app.state.report_service.submit(body.model_dump())
    except OverflowError as error:
        raise HTTPException(429, str(error)) from error
    return {'report_id': report_id, 'status': 'queued'}


@router.get('/reports/{report_id}')
async def read_report(report_id: str, request: Request):
    report = get_report(request, report_id)
    result = report.get('result')
    if result:
        from services.pdf_service import render_markdown
        report['report_html'] = render_markdown(result['final_markdown'])
    return report


@router.get('/reports/{report_id}/events')
async def report_events(report_id: str, request: Request,
                        last_event_id: Annotated[str | None, Header()] = None,
                        after: int = 0):
    get_report(request, report_id)
    try:
        cursor = max(0, after, int(last_event_id or 0))
    except ValueError:
        raise HTTPException(400, '잘못된 이벤트 ID입니다.')

    async def stream():
        nonlocal cursor
        ticks = 0
        while not await request.is_disconnected():
            # Read status before events so a terminal event cannot be missed
            # between the event query and the terminal-state check.
            report = get_report(request, report_id)
            for seq, event in request.app.state.report_store.events(report_id, cursor):
                yield f'id: {seq}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n'
                cursor = seq
            if report['status'] in ('completed', 'failed'):
                yield 'data: [DONE]\n\n'
                return
            ticks += 1
            if ticks % 20 == 0:
                yield ': heartbeat\n\n'
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.get('/reports/{report_id}/pdf')
async def report_pdf(report_id: str, request: Request):
    report = get_report(request, report_id)
    if report['status'] != 'completed':
        raise HTTPException(409, '완료된 보고서만 다운로드할 수 있습니다.')
    from services.pdf_service import create_pdf
    try:
        async with request.app.state.pdf_semaphore:
            content = await create_pdf(report['result']['final_markdown'])
    except Exception:
        raise HTTPException(503, 'PDF를 생성하지 못했습니다. Chromium 설치를 확인하고 다운로드를 다시 시도해 주세요.')
    season = report['request']['season'].replace(' ', '_').replace('/', '')
    # Existing reports keep the brand they were actually researched for.
    brand = report['result'].get('brand') or report['result'].get('report', {}).get('brand') or report['request'].get('brand', DEFAULT_BRAND)
    brand = re.sub(r'[^A-Za-z0-9_-]', '_', brand)
    return Response(content, media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="TrendWeave_{brand}_{season}_{report_id}.pdf"'})
