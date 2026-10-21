"""Browser regression check for the page-local conversation lifecycle.

Serve the shipped HTML/JS/CSS through Playwright routes. All report responses
are test data; no running server or external model/search call is required.
"""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
import pytest
from pydantic import TypeAdapter
from browser_support import preview_response

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.browser
@pytest.mark.parametrize('job_status', ['running', 'completed'])
def test_reload_starts_new_conversation(result, job_status):
    from playwright.async_api import async_playwright, expect
    final = TypeAdapter(dict).dump_python(result, mode='json')

    async def verify():
        requests = []
        errors = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                # Old/malformed saved data must neither restore a job nor break startup.
                await page.add_init_script('''
                    sessionStorage.setItem('trendweave-thread', 'old-thread');
                    sessionStorage.setItem('trendweave-active', '{broken-json');
                    sessionStorage.setItem('unrelated-preference', 'keep');
                ''')

                async def route_request(route):
                    request = route.request
                    path = urlparse(request.url).path
                    if path == '/':
                        await route.fulfill(path=ROOT / 'web_chat.html', content_type='text/html')
                    elif path.startswith('/static/'):
                        content_type = 'text/css' if path.endswith('.css') else 'application/javascript'
                        await route.fulfill(path=ROOT / path.lstrip('/'), content_type=content_type)
                    elif path == '/health':
                        await route.fulfill(json={'analysis_ready': True, 'brand': 'ZARA'})
                    elif path == '/reports/preview':
                        await route.fulfill(json=preview_response(request))
                    elif path == '/reports' and request.method == 'POST':
                        requests.append(request.post_data_json)
                        await route.fulfill(status=202, json={'report_id': 'reset-test', 'status': 'queued'})
                    elif path == '/reports/reset-test/events':
                        event = {'step': 'completed' if job_status == 'completed' else 'search',
                                 'message': '테스트 분석 진행', 'axis': 'runway'}
                        body = f'id: 1\ndata: {json.dumps(event)}\n\n'
                        await route.fulfill(content_type='text/event-stream', body=body)
                    elif path == '/reports/reset-test':
                        await route.fulfill(json={'status': job_status, 'result': final if job_status == 'completed' else None,
                                                  'report_html': '<h1>ZARA 보고서</h1>', 'error': None,
                                                  'request': {'brand': 'ZARA'}})
                    else:
                        raise AssertionError(f'Unexpected request: {path}')

                await page.route('**/*', route_request)
                await page.goto('http://localhost:8765/')
                button = page.get_by_role('button', name='예시 질문으로 시작')
                await page.wait_for_function("!document.querySelector('.example button').disabled")
                assert await page.locator('.message').count() == 0
                await button.click()
                await page.get_by_label('분석 시즌',exact=True).fill('2027 F/W')
                await page.get_by_role('button',name='확인하고 보고서 생성').click()
                await page.locator('.message.report').wait_for()
                if job_status == 'completed':
                    await page.get_by_role('button', name='전체 보고서 보기').wait_for()
                    await page.get_by_role('textbox', name='상품기획 요청').fill('미전송 초안')
                    await page.get_by_role('button', name='전체 보고서 보기').click()
                    await page.get_by_role('dialog').wait_for()
                else:
                    await expect(page.locator('.progress')).to_contain_text('테스트 분석 진행')
                    assert await page.get_by_role('button', name='분석 방향 제안', exact=True).is_disabled()
                assert len(requests) == 1
                first_thread = requests[0]['thread_id']
                assert first_thread != 'old-thread'

                report_fetches = []
                page.on('request', lambda request: report_fetches.append(request.url)
                        if '/reports/' in request.url else None)
                await page.reload()
                await page.wait_for_function("!document.querySelector('.example button').disabled")
                assert await page.locator('.message').count() == 0
                assert await page.locator('.welcome').is_visible()
                assert await page.get_by_role('dialog').count() == 0
                assert await page.get_by_role('textbox', name='상품기획 요청').input_value() == ''
                assert await page.get_by_label('분석 시즌',exact=True).count() == 0
                assert report_fetches == []  # No old job lookup or SSE resubscription.
                assert len(requests) == 1  # No duplicate generation on reload.
                assert await page.evaluate("sessionStorage.getItem('trendweave-active')") is None
                assert await page.evaluate("sessionStorage.getItem('trendweave-thread')") is None
                assert await page.evaluate("sessionStorage.getItem('unrelated-preference')") == 'keep'

                await button.click()
                await page.get_by_role('button',name='확인하고 보고서 생성').click()
                await page.locator('.message.report').wait_for()
                assert len(requests) == 2
                assert requests[1]['thread_id'] != first_thread
                assert not errors
            finally:
                await browser.close()
    asyncio.run(verify())
