"""Conversation UX with deterministic report responses; no live API calls."""
import asyncio
from pathlib import Path
from urllib.parse import urlparse
import pytest
from pydantic import TypeAdapter
from browser_support import preview_response

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.browser
@pytest.mark.parametrize('viewport', [{'width':1280,'height':800}, {'width':390,'height':844}])
def test_conversation_continues_and_preserves_history(result, viewport):
    from playwright.async_api import async_playwright, expect
    final = TypeAdapter(dict).dump_python(result, mode='json')

    async def verify():
        requests, pdf_requests, errors = [], [], []
        statuses = {}
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport=viewport)
                page.on('pageerror', lambda error: errors.append(str(error)))
                # Controllable events let us finish one request while the user
                # is reading a previous report, without a real model or SSE server.
                await page.add_init_script('''
                    window.testStreams = [];
                    window.EventSource = class {
                        constructor(url) { this.url=url; this.closed=false; testStreams.push(this); }
                        close() { this.closed=true; }
                    };
                    window.emitReportEvent = (id,event,seq) => {
                        for (const stream of testStreams) {
                            if (!stream.closed && stream.url.includes('/'+id+'/events'))
                                stream.onmessage?.({data:JSON.stringify(event),lastEventId:String(seq)});
                        }
                    };
                ''')

                async def route_request(route):
                    request = route.request
                    path = urlparse(request.url).path
                    if path == '/':
                        await route.fulfill(path=ROOT/'web_chat.html', content_type='text/html')
                    elif path.startswith('/static/'):
                        await route.fulfill(path=ROOT/path.lstrip('/'),
                            content_type='text/css' if path.endswith('.css') else 'application/javascript')
                    elif path == '/health':
                        await route.fulfill(json={'analysis_ready':True,'brand':'ZARA'})
                    elif path == '/reports/preview':
                        await route.fulfill(json=preview_response(request))
                    elif path == '/reports' and request.method == 'POST':
                        requests.append(request.post_data_json)
                        report_id = f'conversation-{len(requests)}'
                        statuses[report_id] = 'running'
                        await route.fulfill(status=202,json={'report_id':report_id,'status':'queued'})
                    elif path.endswith('/pdf'):
                        pdf_requests.append(path)
                        await route.fulfill(content_type='application/pdf',body=b'%PDF-1.4 test download')
                    elif path.startswith('/reports/'):
                        report_id = path.split('/')[2]
                        state = statuses[report_id]
                        data = {**final,'report':{**final['report'],
                            'executive_summary':f'{report_id} 요약\n'+('근거를 확인하며 이전 대화를 읽습니다.\n'*28)}}
                        await route.fulfill(json={'status':state,'request':{'brand':'ZARA'},'error':None,
                            'result':data if state=='completed' else None,
                            'report_html':f'<h1>{report_id} 보고서</h1>'})
                    else:
                        raise AssertionError(f'Unexpected request: {path}')

                await page.route('**/*',route_request)
                await page.goto('http://localhost:8765/')
                await page.wait_for_function("!document.querySelector('.example button').disabled")
                textbox = page.get_by_role('textbox',name='패션 분석 요청')
                send = page.get_by_role('button',name='분석 방향 제안',exact=True)
                await textbox.fill('첫 번째 상품기획 요청')
                await textbox.press('Enter')
                await page.get_by_role('button',name='확인하고 보고서 생성').wait_for()
                async with page.expect_request(lambda request:request.method=='POST' and request.url.endswith('/reports')):
                    await page.get_by_role('button',name='확인하고 보고서 생성').click()
                await page.wait_for_function('testStreams.length === 1')
                await expect(page.locator('.message.user')).to_have_count(1)
                await expect(page.locator('.message.report')).to_have_count(1)
                # The composer stays on screen, even on mobile, while a report runs.
                box = await textbox.bounding_box()
                assert box and box['y']>=0 and box['y']+box['height']<=viewport['height']
                assert await textbox.is_enabled()
                await textbox.fill('두 번째 상품기획 요청')
                assert await send.is_disabled()
                await textbox.press('Enter')
                assert len(requests)==1
                statuses['conversation-1']='completed'
                await page.evaluate("emitReportEvent('conversation-1',{step:'completed',message:'완료'},1)")
                await expect(send).to_be_enabled()
                assert await textbox.input_value()=='두 번째 상품기획 요청'
                assert await textbox.evaluate('(el)=>document.activeElement===el')
                await textbox.press('Enter')
                await page.get_by_role('button',name='확인하고 보고서 생성').wait_for()
                async with page.expect_request(lambda request:request.method=='POST' and request.url.endswith('/reports')):
                    await page.get_by_role('button',name='확인하고 보고서 생성').click()
                await page.wait_for_function('testStreams.length === 2')
                await expect(page.locator('.message.user')).to_have_count(2)
                await expect(page.locator('.message.report')).to_have_count(2)
                assert requests[0]['thread_id']==requests[1]['thread_id']
                assert requests[1]['planning_request']=='두 번째 상품기획 요청'

                # Reading history must not be interrupted by new progress updates.
                await page.locator('.conversation').evaluate('el=>{el.scrollTop=0;el.dispatchEvent(new Event("scroll"));}')
                await page.evaluate("emitReportEvent('conversation-2',{step:'search',axis:'runway',message:'새 진행 상태'},1)")
                latest = page.get_by_role('button',name='최신 대화로 이동')
                await expect(latest).to_be_visible()
                assert await page.locator('.conversation').evaluate('el=>el.scrollTop')==0
                await latest.click()
                await page.wait_for_function('(()=>{const el=document.querySelector(".conversation");return el.scrollHeight-el.scrollTop-el.clientHeight<80})()')
                await expect(latest).to_have_count(0)

                # Open the previous report while the next one completes.
                first_report=page.locator('.message.report').nth(0)
                await first_report.get_by_role('button',name='전체 보고서 보기').click()
                await expect(page.get_by_role('dialog')).to_be_visible()
                statuses['conversation-2']='completed'
                await page.evaluate("emitReportEvent('conversation-2',{step:'completed',message:'완료'},2)")
                await expect(page.locator('.message.report .actions')).to_have_count(2)
                assert await page.get_by_role('dialog').evaluate('el=>el.contains(document.activeElement)')
                assert 'conversation-1 보고서' in await page.get_by_role('dialog').inner_text()
                await page.keyboard.press('Escape')
                async with page.expect_download() as download:
                    await first_report.get_by_role('button',name='PDF 다운로드').click()
                assert (await download.value).suggested_filename.startswith('TrendWeave_ZARA_')
                assert pdf_requests==['/reports/conversation-1/pdf']

                # Shift+Enter and Korean IME confirmation must not send a new request.
                await textbox.fill('줄바꿈')
                await textbox.press('Shift+Enter')
                assert await textbox.input_value()=='줄바꿈\n'
                await textbox.dispatch_event('keydown',{'key':'Enter','isComposing':True,'keyCode':229})
                assert len(requests)==2
                assert await send.is_enabled()
                box=await textbox.bounding_box()
                assert box['y']+box['height']<=viewport['height']
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                if await latest.count():
                    await latest.click()
                artifacts=ROOT/'.artifacts/chat-conversation'
                artifacts.mkdir(parents=True,exist_ok=True)
                await page.screenshot(path=str(artifacts/f'{viewport["width"]}.png'),full_page=True)
                assert not errors
            finally:
                await browser.close()
    asyncio.run(verify())
