"""UI approval/cancel gate; mocked model/API responses, real Vue in Chromium."""
import asyncio
from pathlib import Path
from urllib.parse import urlparse
import pytest
from browser_support import preview_response

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.browser
@pytest.mark.parametrize('viewport',[{'width':1280,'height':900},{'width':390,'height':844}])
def test_review_must_be_approved_before_report(viewport):
    from playwright.async_api import async_playwright, expect
    async def verify():
        previews, approvals, cancellations, errors = [],[],[],[]
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            try:
                page=await browser.new_page(viewport=viewport)
                page.on('pageerror',lambda error:errors.append(str(error)))
                await page.add_init_script('''window.EventSource=class {constructor(url){this.url=url;}close(){}};''')
                async def handle(route):
                    req=route.request;path=urlparse(req.url).path
                    if path=='/':
                        await route.fulfill(path=ROOT/'web_chat.html',content_type='text/html')
                    elif path.startswith('/static/'):
                        await route.fulfill(path=ROOT/path.lstrip('/'),content_type='text/css' if path.endswith('.css') else 'application/javascript')
                    elif path=='/health':
                        await route.fulfill(json={'analysis_ready':True,'brand':'ZARA'})
                    elif path=='/reports/preview':
                        previews.append(req.post_data_json)
                        await route.fulfill(json={**preview_response(req),'preview_id':f'proposal-{len(previews)}'})
                    elif path.endswith('/cancel'):
                        cancellations.append(path)
                        await route.fulfill(json={'status':'cancelled'})
                    elif path=='/reports':
                        approvals.append(req.post_data_json)
                        await route.fulfill(status=202,json={'report_id':'approved-job','status':'queued'})
                    elif path=='/reports/approved-job':
                        await route.fulfill(json={'status':'running','result':None,'request':{'brand':'ZARA'},'events':[],'error':None})
                    else:raise AssertionError(path)
                await page.route('**/*',handle)
                await page.goto('http://localhost:8765/')
                await page.wait_for_function("!document.querySelector('.example button').disabled")
                assert await page.get_by_label('런웨이 시즌',exact=True).count()==0
                textbox=page.get_by_role('textbox',name='패션 분석 요청',exact=True)
                send=page.get_by_role('button',name='분석 방향 제안',exact=True)
                await textbox.fill('다가오는 여름 데님 상품을 기획해줘.')
                await textbox.press('Enter')
                approve=page.get_by_role('button',name='확인하고 보고서 생성')
                await expect(approve).to_be_enabled()
                assert len(previews)==1 and len(approvals)==0
                assert set(previews[0])=={'planning_request','thread_id'}
                assert await page.locator('.message.report').count()==0
                await expect(page.get_by_label('런웨이 시즌',exact=True)).to_have_value('2027 S/S')
                brand=page.get_by_label('브랜드 범위',exact=True)
                assert await brand.input_value()=='ZARA'
                assert await brand.is_enabled()
                await expect(page.locator('.review-time')).to_contain_text('2026-10-21 10:30 (한국시간)')
                await textbox.fill('다음 요청의 초안')
                await expect(send).to_be_disabled()
                await textbox.press('Enter')
                assert len(previews)==1 and len(approvals)==0
                await page.get_by_role('button',name='생성 취소').click()
                await expect(send).to_be_enabled()
                assert cancellations==['/reports/preview/proposal-1/cancel']
                assert len(approvals)==0
                await expect(page.locator('.review-summary')).to_contain_text('보고서 생성을 취소')
                await textbox.press('Enter')
                await expect(approve).to_be_enabled()
                season=page.get_by_label('런웨이 시즌',exact=True)
                await season.fill('2028 FW')
                await season.press('Enter')
                assert len(approvals)==0  # Editing a field is not approval.
                await page.get_by_label('패션 분석 요청 확인').fill('린넨 셔츠 중심으로 기획해줘.')
                await page.get_by_label('보고서 방향 확인').fill('한국 일상복용 가벼운 린넨 셔츠 상품 방향 3개를 제안한다.')
                artifacts=ROOT/'.artifacts/review'
                artifacts.mkdir(parents=True,exist_ok=True)
                await page.locator('.preview').last.locator('h3').scroll_into_view_if_needed()
                await page.screenshot(path=str(artifacts/f'{viewport["width"]}.png'),full_page=True)
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await expect(approve).to_be_disabled()
                await page.get_by_role('button',name='제안 다시 받기',exact=True).click()
                await expect(approve).to_be_enabled()
                await approve.evaluate('el=>{el.click();el.click();}')
                await expect(page.locator('.message.report')).to_have_count(1)
                assert len(approvals)==1
                assert approvals[0]['approved'] is True
                assert approvals[0]['runway_season']=='2028 F/W'
                assert approvals[0]['brand']=='ZARA'
                assert approvals[0]['preview_id']=='proposal-3'
                assert approvals[0]['planning_request']=='린넨 셔츠 중심으로 기획해줘.'
                assert '린넨 셔츠' in approvals[0]['report_direction']
                await expect(page.locator('.message.report h3')).to_contain_text('2028 F/W · 상품기획 전략 보고서 · ZARA')
                assert not errors
            finally:await browser.close()
    asyncio.run(verify())


@pytest.mark.browser
def test_review_errors_preserve_edits_and_reload_resets():
    from playwright.async_api import async_playwright, expect
    async def verify():
        proposals, approvals=[],[]
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            try:
                page=await browser.new_page()
                async def handle(route):
                    req=route.request;path=urlparse(req.url).path
                    if path=='/':await route.fulfill(path=ROOT/'web_chat.html',content_type='text/html')
                    elif path.startswith('/static/'):
                        await route.fulfill(path=ROOT/path.lstrip('/'),content_type='text/css' if path.endswith('.css') else 'application/javascript')
                    elif path=='/health':await route.fulfill(json={'analysis_ready':True,'brand':'ZARA'})
                    elif path=='/reports/preview':
                        proposals.append(req.post_data_json)
                        if len(proposals)==1:await route.fulfill(status=503,json={'detail':'제안 실패. 보고서는 생성하지 않았습니다.'})
                        else:await route.fulfill(json=preview_response(req))
                    elif path.endswith('/cancel'):
                        await route.fulfill(json={'status':'cancelled'})
                    elif path=='/reports':
                        approvals.append(req.post_data_json)
                        await route.fulfill(status=429,json={'detail':'현재 대기 작업이 많습니다. 다시 시도해 주세요.'})
                    else:raise AssertionError(path)
                await page.route('**/*',handle)
                await page.goto('http://localhost:8765/')
                await page.wait_for_function("!document.querySelector('.example button').disabled")
                await page.get_by_role('textbox',name='패션 분석 요청',exact=True).fill('상품기획 요청')
                await page.get_by_role('textbox',name='패션 분석 요청',exact=True).press('Enter')
                await expect(page.locator('.notice.error')).to_contain_text('제안 실패')
                assert approvals==[]
                await page.get_by_role('button',name='분석 방향 다시 제안받기').click()
                season=page.get_by_label('런웨이 시즌',exact=True)
                await season.fill('2029 S/S')
                await page.get_by_label('보고서 방향 확인').fill('수정한 방향')
                await page.get_by_role('button',name='제안 다시 받기',exact=True).click()
                await expect(page.get_by_role('button',name='확인하고 보고서 생성')).to_be_enabled()
                await page.get_by_role('button',name='확인하고 보고서 생성').click()
                await expect(page.locator('.notice.error').last).to_contain_text('대기 작업')
                await expect(season).to_have_value('2029 S/S')
                await expect(page.get_by_label('보고서 방향 확인')).to_have_value('수정한 방향')
                assert await page.locator('.message.report').count()==0
                assert await page.get_by_role('button',name='확인하고 보고서 생성').is_enabled()
                await page.reload()
                await page.wait_for_function("!document.querySelector('.example button').disabled")
                assert await page.locator('.message').count()==0
                assert len(approvals)==1
                assert await page.locator('.welcome').is_visible()
            finally:await browser.close()
    asyncio.run(verify())
