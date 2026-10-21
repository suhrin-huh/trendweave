import asyncio
import json
import logging
import re
from typing import Annotated, Literal
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.trendweave.config import DEFAULT_BRAND
from agent.trendweave.review import normalize_season, request_time

router = APIRouter()


class ThreadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    thread_id: str = Field(default='web', min_length=1, max_length=100)

    @field_validator('thread_id', mode='before')
    @classmethod
    def strip_thread(cls, value):
        return value.strip() if isinstance(value, str) else value


class PreviewRequest(ThreadRequest):
    planning_request: str = Field(min_length=1, max_length=4000)

    @field_validator('planning_request', mode='before')
    @classmethod
    def strip_request(cls, value):
        return value.strip() if isinstance(value, str) else value


class ReportRequest(PreviewRequest):
    preview_id: str = Field(min_length=1, max_length=100)
    approved: Literal[True]
    brand: Literal['ZARA']
    season: str = Field(min_length=4, max_length=20)
    report_direction: str = Field(min_length=1, max_length=1500)

    @field_validator('season')
    @classmethod
    def season_format(cls, value):
        return normalize_season(value)

    @field_validator('report_direction', mode='before')
    @classmethod
    def strip_direction(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.post('/reports/preview')
async def preview_report(body: PreviewRequest, request: Request):
    if request.app.state.trend_error:
        raise HTTPException(503, request.app.state.trend_error)
    requested_at = request_time()
    try:
        async with request.app.state.preview_semaphore:
            return await asyncio.to_thread(request.app.state.preview_service.propose,
                body.planning_request, body.thread_id, requested_at)
    except Exception:
        logging.getLogger(__name__).exception('Report preview failed')
        raise HTTPException(503, '분석 방향을 제안하지 못했습니다. 모델 설정·호출 한도를 확인하고 다시 시도해 주세요. 보고서는 생성하지 않았습니다.')


def get_preview(request, preview_id, thread_id):
    preview = request.app.state.report_store.get_preview(preview_id)
    if preview is None or preview['thread_id'] != thread_id:
        raise HTTPException(404, '해당 대화의 분석 제안을 찾을 수 없습니다.')
    return preview


@router.post('/reports/preview/{preview_id}/cancel')
async def cancel_preview(preview_id: str, body: ThreadRequest, request: Request):
    store = request.app.state.report_store
    with store.lock:
        preview = get_preview(request, preview_id, body.thread_id)
        if preview['status'] == 'approved':
            raise HTTPException(409, '이미 승인하여 생성 중인 보고서입니다.')
        store.cancel_preview(preview_id)
    return {'status': 'cancelled'}


def get_report(request, report_id):
    report = request.app.state.report_store.get(report_id)
    if report is None:
        raise HTTPException(404, '보고서를 찾을 수 없습니다.')
    return report


@router.post('/reports', status_code=202)
async def create_report(body: ReportRequest, request: Request):
    if request.app.state.trend_error:
        raise HTTPException(503, request.app.state.trend_error)
    store = request.app.state.report_store
    # The store lock serializes approval/cancellation. Job insertion and the
    # pending->approved update also share one SQLite transaction in create().
    with store.lock:
        preview = get_preview(request, body.preview_id, body.thread_id)
        payload = {**body.model_dump(), 'requested_at': preview['payload']['requested_at']}
        if preview['status'] == 'approved':
            if preview['approval'] != payload:
                raise HTTPException(409, '이미 다른 내용으로 승인한 제안입니다. 새 질문으로 다시 확인해 주세요.')
            return {'report_id': preview['report_id'], 'status': store.get(preview['report_id'])['status']}
        if preview['status'] != 'pending':
            raise HTTPException(409, '취소한 제안입니다. 새 질문으로 다시 확인해 주세요.')
        try:
            report_id = request.app.state.report_service.submit(payload, preview_id=body.preview_id)
        except OverflowError as error:
            raise HTTPException(429, str(error)) from error
    return {'report_id': report_id, 'status': 'queued'}


@router.get('/reports/{report_id}')
async def read_report(report_id: str, request: Request):
    report = get_report(request, report_id)
    # Polling may observe completion before the last SSE frames arrive. Return
    # the persisted history so closing that stream does not lose earlier steps.
    report['events'] = [{**event, 'id': seq} for seq, event in
                        request.app.state.report_store.events(report_id)]
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
