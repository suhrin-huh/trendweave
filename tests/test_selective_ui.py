"""Real Vue verifies domain notices and editable research selection."""
import asyncio
from pathlib import Path
from urllib.parse import urlparse
import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.browser
@pytest.mark.parametrize('width',[1280,390])
def test_domain_gate_and_selected_fields_in_browser(width):
    from playwright.async_api import async_playwright, expect
    async def verify():
        approvals=[]
        errors=[]
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            try:
                page=await browser.new_page(viewport={'width':width,'height':900})
                page.on('pageerror',lambda error:errors.append(str(error)))
                await page.add_init_script('window.EventSource=class {close(){}};')
                async def route_request(route):
                    request=route.request
                    path=urlparse(request.url).path
                    if path=='/':await route.fulfill(path=ROOT/'web_chat.html',content_type='text/html')
                    elif path.startswith('/static/'):
                        await route.fulfill(path=ROOT/path.lstrip('/'),content_type='text/css' if path.endswith('.css') else 'application/javascript')
                    elif path=='/health':await route.fulfill(json={'analysis_ready':True,'brand':'ZARA'})
                    elif path=='/reports/preview':
                        question=request.post_data_json['planning_request']
                        if '파이썬' in question:
                            await route.fulfill(json={'status':'out_of_scope','domain_status':'non_fashion',
                                'message':'패션 관련 질문만 도와드릴 수 있어요.','requested_at':'2026-10-21T10:30:00+09:00'})
                        else:
                            await route.fulfill(json={'status':'pending','domain_status':'fashion','preview_id':'styling',
                                'season':'2027 S/S','brand':None,'report_type':'styling','selected_axes':['color','market'],
                                'axis_reasons':{'color':'컬러 조합 확인','market':'현재 스타일 확인'},'reason':'출근 코디 제안',
                                'planning_request':question,'report_direction':'여름 출근 코디 추천','requested_at':'2026-10-21T10:30:00+09:00'})
                    elif path=='/reports':
                        approvals.append(request.post_data_json)
                        await route.fulfill(status=202,json={'report_id':'selected','status':'queued'})
                    elif path=='/reports/selected':
                        await route.fulfill(json={'status':'running','result':None,'request':approvals[-1],'events':[]})
                    else:raise AssertionError(path)
                await page.route('**/*',route_request)
                await page.goto('http://localhost:8765/')
                await page.wait_for_function("!document.querySelector('.example button').disabled")
                textbox=page.get_by_role('textbox',name='패션 분석 요청',exact=True)
                await textbox.fill('파이썬 코드 작성')
                await textbox.press('Enter')
                await expect(page.locator('.preview .notice')).to_contain_text('패션 관련 질문만')
                assert await page.get_by_role('button',name='확인하고 보고서 생성').count()==0
                assert await page.locator('.message.report').count()==0
                assert not approvals
                await textbox.fill('여름 출근용 데님 코디 추천')
                await textbox.press('Enter')
                await expect(page.get_by_label('보고서 유형',exact=True)).to_have_value('styling')
                await expect(page.get_by_label('분석 브랜드',exact=True)).to_have_value('브랜드 조사 없음')
                assert await page.get_by_role('checkbox',name='컬러',exact=False).is_disabled()
                assert await page.get_by_role('checkbox',name='시장',exact=False).is_disabled()
                await page.get_by_role('checkbox',name='런웨이',exact=False).check()
                await page.get_by_label('보고서 유형',exact=True).select_option('trend_analysis')
                await expect(page.get_by_role('checkbox',name='컬러',exact=False)).to_be_checked()
                assert not await page.get_by_role('checkbox',name='시장',exact=False).is_checked()
                assert not await page.get_by_role('checkbox',name='런웨이',exact=False).is_checked()
                await page.get_by_role('checkbox',name='컬러',exact=False).uncheck()
                await expect(page.get_by_role('button',name='확인하고 보고서 생성')).to_be_disabled()
                await page.get_by_role('checkbox',name='컬러',exact=False).check()
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                artifacts=ROOT/'.artifacts/selective-ui'
                artifacts.mkdir(parents=True,exist_ok=True)
                await page.screenshot(path=str(artifacts/f'{width}.png'),full_page=True)
                await page.get_by_role('button',name='확인하고 보고서 생성').click()
                await expect(page.locator('.message.report')).to_have_count(1)
                assert len(approvals)==1 and approvals[0]['selected_axes']==['color']
                assert approvals[0]['report_type']=='trend_analysis' and approvals[0]['brand'] is None
                await expect(page.locator('.message.report h3')).to_contain_text('트렌드 분석서')
                assert 'ZARA' not in await page.locator('.message.report h3').inner_text()
                assert not errors
            finally:await browser.close()
    asyncio.run(verify())
