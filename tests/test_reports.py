import asyncio
import io
import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
import pytest
from services.report_routes import router
from services.report_service import ReportService
from services.report_store import ReportStore
from services.preview_service import PreviewService
from services.pdf_service import render_markdown


@pytest.fixture
def report_app(tmp_path, result):
    final=TypeAdapter(dict).dump_python(result,mode='json')
    store=ReportStore(tmp_path/'reports.db')
    @asynccontextmanager
    async def lifespan(app):
        app.state.report_store=store
        app.state.report_service=ReportService(store,runner=lambda request,emit:final)
        app.state.trend_error=None
        app.state.preview_service=PreviewService(store)
        app.state.preview_semaphore=asyncio.Semaphore(1)
        app.state.pdf_semaphore=asyncio.Semaphore(1)
        yield
        app.state.report_service.close()
        store.close()
    app=FastAPI(lifespan=lifespan)
    app.include_router(router)
    return app


def completed(client):
    preview=client.post('/reports/preview',json={'planning_request':'일상 상품 기획'}).json()
    response=client.post('/reports',json={
        'preview_id':preview['preview_id'],'approved':True,'season':'2027 SS','brand':'ZARA',
        'planning_request':'일상 상품 기획','report_direction':preview['report_direction']})
    assert response.status_code==202
    report_id=response.json()['report_id']
    for _ in range(100):
        report=client.get('/reports/'+report_id).json()
        if report['status'] in ('completed','failed'):
            assert report['status']=='completed'
            return report_id,report
        time.sleep(.01)
    pytest.fail('Job did not complete')


def test_creation_and_replay(report_app):
    with TestClient(report_app) as client:
        report_id,report=completed(client)
        assert report['request']['season']=='2027 S/S'
        assert report['request']['brand']=='ZARA'
        assert '<table>' in report['report_html']
        persisted=report_app.state.report_store.events(report_id)
        assert report['events']==[{**event,'id':seq} for seq,event in persisted]
        assert report['events'][0]['step']=='queued'
        assert report['events'][-1]['step']=='completed'
        content=client.get(f'/reports/{report_id}/events').text
        assert 'id: 1' in content and '[DONE]' in content
        cursor=report_app.state.report_store.events(report_id)[-1][0]
        replay=client.get(f'/reports/{report_id}/events',headers={'Last-Event-ID':str(cursor)}).text
        assert 'id:' not in replay
        assert '[DONE]' in replay
        assert client.get('/reports/absent').status_code==404
        assert client.get('/reports/absent/pdf').status_code==404
        assert client.get(f'/reports/{report_id}/events',headers={'Last-Event-ID':'bad'}).status_code==400


def test_validation_and_unavailable(report_app):
    with TestClient(report_app) as client:
        for request in [ {'season':'2027 SS','planning_request':'   '},
                         {'season':'tomorrow','planning_request':'기획'},
                         {'season':'2027 SS','planning_request':'x'*4001}]:
            assert client.post('/reports',json=request).status_code==422
        report_app.state.trend_error='API 키를 설정하세요.'
        assert client.post('/reports/preview',json={'planning_request':'기획'}).status_code==503


def test_restart_isolation_and_failure(tmp_path):
    path=tmp_path/'jobs.db'
    store=ReportStore(path)
    first=store.create({'season':'2027 S/S'})
    second=store.create({'season':'2027 F/W'})
    store.complete(second,{'final_markdown':'saved'})
    assert first!=second
    store.event(first,{'step':'search','message':'first only'})
    assert not any(e['message']=='first only' for _,e in store.events(second))
    store.close()
    store=ReportStore(path)
    assert store.get(first)['status']=='failed'
    assert '재시작' in store.get(first)['error']
    assert store.get(second)['result']['final_markdown']=='saved'
    store.close()


def test_job_failure_and_queue_bound(tmp_path):
    from threading import Event
    gate=Event()
    def runner(request,emit):
        gate.wait(3)
        raise RuntimeError('private provider message')
    store=ReportStore(tmp_path/'jobs.db')
    service=ReportService(store,workers=1,runner=runner)
    try:
        ids=[service.submit({'planning_request':str(i)}) for i in range(3)]
        with pytest.raises(OverflowError):
            service.submit({})
        gate.set()
        service.close()
        assert all(store.get(report_id)['status']=='failed' for report_id in ids)
        assert all('private' not in store.get(report_id)['error'] for report_id in ids)
    finally:
        gate.set();service.close();store.close()


def test_markdown_sanitization():
    html=render_markdown('''# 한글\n<script>alert('bad')</script>\n<img src=x onerror=alert(1)>\n<a href="javascript:alert(1)">bad</a>\n<details><summary>Trace</summary>내용</details>''')
    assert '<script' not in html and '<img' not in html and 'javascript:' not in html
    assert '<details>' in html and '한글' in html


def test_pdf_error_keeps_report_complete(report_app,monkeypatch):
    async def fail(_):
        raise RuntimeError('missing browser')
    monkeypatch.setattr('services.pdf_service.create_pdf',fail)
    with TestClient(report_app) as client:
        report_id,_=completed(client)
        assert client.get(f'/reports/{report_id}/pdf').status_code==503
        assert client.get(f'/reports/{report_id}').json()['status']=='completed'


@pytest.mark.browser
def test_korean_pdf(report_app):
    from pypdf import PdfReader
    with TestClient(report_app) as client:
        report_id,report=completed(client)
        response=client.get(f'/reports/{report_id}/pdf')
        assert response.status_code==200,response.text[:300] if response.status_code!=200 else ''
        assert response.content.startswith(b'%PDF')
        assert 'attachment' in response.headers['content-disposition']
        assert 'TrendWeave_ZARA_' in response.headers['content-disposition']
        reader=PdfReader(io.BytesIO(response.content))
        text='\n'.join(page.extract_text() for page in reader.pages)
        assert len(reader.pages)>=2
        assert '일상에서' in text
        assert 'Agent Trace' in text and 'References' in text
        assert '재검색' not in report['result']['final_markdown'] or '재검색' in text
        # Embedded font resource is present on the generated pages.
        assert any(page['/Resources'].get('/Font') for page in reader.pages)


def test_existing_report_pdf_preserves_original_brand(report_app,monkeypatch):
    async def pdf(_):
        return b'%PDF-test'
    monkeypatch.setattr('services.pdf_service.create_pdf',pdf)
    with TestClient(report_app) as client:
        report_id=report_app.state.report_store.create({'season':'2027 S/S'})
        report_app.state.report_store.complete(report_id,{'brand':'UNIQLO','final_markdown':'# UNIQLO'})
        response=client.get(f'/reports/{report_id}/pdf')
        assert response.status_code==200
        assert 'TrendWeave_UNIQLO_' in response.headers['content-disposition']
