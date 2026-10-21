"""Accumulated analysis history, folding, and persisted event reconciliation."""
import asyncio
from pathlib import Path
from urllib.parse import urlparse
import pytest
from pydantic import TypeAdapter
from browser_support import preview_response

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.browser
@pytest.mark.parametrize('viewport', [{'width':1280,'height':800}, {'width':390,'height':844}])
def test_analysis_history_accumulates_and_can_be_folded(result, viewport):
    from playwright.async_api import async_playwright, expect
    final = TypeAdapter(dict).dump_python(result, mode='json')

    async def verify():
        records, statuses, requests, errors = {}, {}, [], []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport=viewport)
                page.on('pageerror',lambda error:errors.append(str(error)))
                await page.add_init_script('''
                    window.testStreams=[];
                    window.EventSource=class {
                        constructor(url){this.url=url;this.closed=false;testStreams.push(this);}
                        close(){this.closed=true;}
                    };
                    window.emitHistory=(id,event,seq)=>{
                        for(const stream of testStreams)
                            if(!stream.closed && stream.url.includes('/'+id+'/events'))
                                stream.onmessage?.({data:JSON.stringify(event),lastEventId:String(seq)});
                    };
                ''')

                async def route_request(route):
                    request = route.request
                    path = urlparse(request.url).path
                    if path == '/':
                        await route.fulfill(path=ROOT/'web_chat.html',content_type='text/html')
                    elif path.startswith('/static/'):
                        await route.fulfill(path=ROOT/path.lstrip('/'),
                            content_type='text/css' if path.endswith('.css') else 'application/javascript')
                    elif path == '/health':
                        await route.fulfill(json={'analysis_ready':True,'brand':'ZARA'})
                    elif path == '/reports/preview':
                        await route.fulfill(json=preview_response(request))
                    elif path == '/reports' and request.method == 'POST':
                        requests.append(request.post_data_json)
                        report_id=f'history-{len(requests)}'
                        records[report_id]=[]
                        statuses[report_id]='running'
                        await route.fulfill(status=202,json={'report_id':report_id,'status':'queued'})
                    elif path.startswith('/reports/'):
                        report_id=path.split('/')[2]
                        state=statuses[report_id]
                        await route.fulfill(json={'status':state,'events':records[report_id],
                            'request':{'brand':'ZARA'},'error':'테스트 분석 실패' if state=='failed' else None,
                            'result':final if state=='completed' else None,'report_html':'<h1>ZARA 보고서</h1>'})
                    else:
                        raise AssertionError(path)

                async def emit(seq,step,message,axis=''):
                    event={'id':seq,'step':step,'message':message,'axis':axis}
                    if not any(value['id']==seq for value in records['history-1']):
                        records['history-1'].append(event)
                    await page.evaluate('(e)=>emitHistory("history-1",e,e.id)',event)

                await page.route('**/*',route_request)
                await page.goto('http://localhost:8765/')
                await page.wait_for_function("!document.querySelector('.example button').disabled")
                await page.get_by_role('textbox',name='상품기획 요청').fill('누적 분석 과정 확인')
                await page.get_by_role('textbox',name='상품기획 요청').press('Enter')
                await page.get_by_role('button',name='확인하고 보고서 생성').click()
                await page.wait_for_function('testStreams.length===1')
                first=page.locator('.message.report').nth(0)
                toggle=first.get_by_role('button',name='분석 과정')
                await expect(toggle).to_have_attribute('aria-expanded','true')
                await expect(first.locator('.activity-empty')).to_be_visible()
                await emit(1,'normalizer','요청을 정리했습니다.')
                await emit(2,'planner','검색 계획을 만들었습니다.')
                await emit(3,'search','런웨이 첫 번째 검색','runway')
                await emit(4,'search','컬러 첫 번째 검색','color')
                timeline=first.get_by_role('list',name='분석 진행 기록')
                await expect(timeline.locator('li')).to_have_count(4)
                assert '요청을 정리했습니다.' in await timeline.inner_text()
                assert '검색 계획을 만들었습니다.' in await timeline.inner_text()
                assert '런웨이' in await timeline.inner_text() and '컬러' in await timeline.inner_text()
                await toggle.click()
                await expect(toggle).to_have_attribute('aria-expanded','false')
                await expect(timeline).not_to_be_visible()
                await emit(5,'grade','런웨이 문서 평가','runway')
                await expect(toggle).to_contain_text('5개 기록')
                await expect(toggle).to_have_attribute('aria-expanded','false')
                await toggle.press('Enter')
                await expect(timeline).to_be_visible()
                await expect(timeline.locator('li')).to_have_count(5)
                await toggle.press('Space')
                await expect(toggle).to_have_attribute('aria-expanded','false')
                await toggle.press('Enter')
                # Replayed and out-of-order SSE must not duplicate or discard records.
                await emit(3,'search','중복 이벤트는 표시하지 않습니다.','runway')
                await expect(timeline.locator('li')).to_have_count(5)
                await emit(7,'rewrite','검색어를 다시 작성했습니다.','color')
                await emit(6,'extract','런웨이 신호를 추출했습니다.','runway')
                await expect(timeline.locator('li')).to_have_count(7)
                assert '신호를 추출' in await timeline.locator('li').nth(5).inner_text()
                assert '검색어를 다시 작성' in await timeline.locator('li').nth(6).inner_text()
                for seq in range(8,25):
                    await emit(seq,'grade',f'누적 문서 평가 기록 {seq}','market')
                await expect(timeline.locator('li')).to_have_count(24)
                assert await timeline.evaluate('el=>el.scrollHeight>el.clientHeight')
                await timeline.evaluate('el=>el.scrollTop=0')
                await emit(25,'extract','브랜드 신호 추출','brand')
                await expect(timeline.locator('li')).to_have_count(25)
                assert await timeline.evaluate('el=>el.scrollTop')==0

                # Polling can finish first: retrieve all persisted records, including
                # terminal events never delivered over SSE, while keeping it folded.
                await toggle.click()
                statuses['history-1']='completed'
                records['history-1'] += [
                    {'id':26,'step':'strategy','message':'전략 작성 완료'},
                    {'id':27,'step':'critic','message':'전략 검토 통과'},
                    {'id':28,'step':'completed','message':'분석 완료'}]
                await page.evaluate('testStreams[0].onerror()')
                await first.get_by_role('button',name='상태 다시 확인').click()
                await expect(first.get_by_role('button',name='전체 보고서 보기')).to_be_visible()
                await expect(toggle).to_contain_text('28개 기록')
                await expect(toggle).to_have_attribute('aria-expanded','false')
                await toggle.click()
                await expect(timeline.locator('li')).to_have_count(28)
                assert '전략 작성 완료' in await timeline.inner_text()
                assert '분석 완료' in await timeline.locator('li').last.inner_text()
                artifacts=ROOT/'.artifacts/analysis-history'
                artifacts.mkdir(parents=True,exist_ok=True)
                await timeline.scroll_into_view_if_needed()
                await page.screenshot(path=str(artifacts/f'{viewport["width"]}.png'),full_page=True)
                await toggle.click()

                # Each report keeps its own fold state and its own failed-job history.
                await page.get_by_role('textbox',name='상품기획 요청').fill('두 번째 요청')
                await page.get_by_role('textbox',name='상품기획 요청').press('Enter')
                await page.get_by_role('button',name='확인하고 보고서 생성').click()
                await page.wait_for_function('testStreams.length===2')
                statuses['history-2']='failed'
                records['history-2']=[{'id':1,'step':'queued','message':'두 번째 분석 대기'},
                                      {'id':2,'step':'failed','message':'테스트 분석 실패'}]
                await page.evaluate('testStreams[1].onerror()')
                second=page.locator('.message.report').nth(1)
                await second.get_by_role('button',name='상태 다시 확인').click()
                await expect(second.locator('.notice.error')).to_contain_text('테스트 분석 실패')
                await expect(second.get_by_role('list',name='분석 진행 기록').locator('li')).to_have_count(2)
                await expect(toggle).to_contain_text('28개 기록')
                await expect(toggle).to_have_attribute('aria-expanded','false')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not errors
            finally:
                await browser.close()
    asyncio.run(verify())
