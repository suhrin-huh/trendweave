"""Live end-to-end verification through the shipped Vue UI and proxy.

Runs actual Gemini/Tavily analysis once; artifacts are local and ignored by Git.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import httpx
from playwright.async_api import async_playwright
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.artifacts'


async def verify():
    errors=[]
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch()
        try:
            page=await browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto('http://127.0.0.1:8600')
            button=page.get_by_role('button',name='예시 질문으로 시작')
            await button.wait_for()
            await page.wait_for_function("!document.querySelector('.example button').disabled")
            await page.screenshot(path=str(ARTIFACTS/'desktop.png'),full_page=True)
            await page.set_viewport_size({'width':390,'height':844})
            await page.screenshot(path=str(ARTIFACTS/'mobile.png'),full_page=True)
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            await page.set_viewport_size({'width':1440,'height':1000})
            await button.click()
            await page.get_by_role('button',name='확인하고 보고서 생성').wait_for(timeout=120000)
            await page.screenshot(path=str(ARTIFACTS/'review-preview.png'),full_page=True)
            async with page.expect_response(lambda response:response.url.endswith('/reports') and response.request.method=='POST') as created:
                await page.get_by_role('button',name='확인하고 보고서 생성').click()
            response=await created.value
            assert response.status==202,await response.text()
            report_id=(await response.json())['report_id']
            assert await page.get_by_role('button',name='분석 방향 제안',exact=True).is_disabled()
            # Keep this page open while the job runs. Reload now resets the chat.
            for _ in range(180):
                status=await page.request.get(f'http://127.0.0.1:8600/reports/{report_id}')
                report=await status.json()
                if report['status'] in ('completed','failed'):
                    assert report['status']=='completed',report.get('error')
                    break
                await asyncio.sleep(5)
            else:
                raise AssertionError('Analysis exceeded browser verification timeout')
            await page.get_by_role('button',name='전체 보고서 보기').wait_for(timeout=15000)
            await page.screenshot(path=str(ARTIFACTS/'completed.png'),full_page=True)
            await page.get_by_role('button',name='전체 보고서 보기').click()
            await page.get_by_role('dialog').wait_for()
            await page.screenshot(path=str(ARTIFACTS/'report-preview.png'),full_page=True)
            async with page.expect_download(timeout=120000) as downloaded:
                await page.get_by_role('dialog').get_by_role('button',name='PDF 다운로드').click()
            download=await downloaded.value
            pdf_path=ARTIFACTS/'browser-report.pdf'
            await download.save_as(str(pdf_path))
            reader=PdfReader(pdf_path)
            text='\n'.join(p.extract_text() for p in reader.pages)
            assert '상품기획' in text and 'References' in text and 'Agent Trace' in text
            assert len(report['result']['report']['product_directions'])==3
            assert set(report['result']['research_results'])=={'runway','color','market','brand'}
            await page.keyboard.press('Escape')
            assert await page.get_by_role('dialog').count()==0
            # Verify keyboard and smaller viewport report rendering too.
            await page.set_viewport_size({'width':390,'height':844})
            await page.get_by_role('button',name='전체 보고서 보기').click()
            await page.screenshot(path=str(ARTIFACTS/'mobile-report.png'),full_page=True)
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            await page.reload()
            await page.wait_for_function("!document.querySelector('.example button').disabled")
            assert await page.locator('.message').count()==0
            assert await page.get_by_role('dialog').count()==0
            assert await page.get_by_role('textbox',name='상품기획 요청').input_value()==''
            assert await page.locator('.welcome').is_visible()
            # Resetting the view does not delete the persisted report.
            saved=await page.request.get(f'http://127.0.0.1:8600/reports/{report_id}')
            assert (await saved.json())['status']=='completed'
            assert not errors,errors
            (ARTIFACTS/'browser-report.md').write_text(report['result']['final_markdown'])
            (ARTIFACTS/'browser-verification.json').write_text(json.dumps({
                'report_id':report_id,'status':report['status'],'pages':len(reader.pages),
                'critic_passed':report['result']['critique']['passed'],
                'llm_reviewed':report['result']['critique']['llm_reviewed'],
                'revisions':report['result']['revision_count'],
                'quality':{axis:value['confidence'] for axis,value in report['result']['research_results'].items()},
                'browser_errors':errors},ensure_ascii=False,indent=2))
            print('Browser E2E passed; PDF pages:',len(reader.pages),flush=True)
        finally:
            await browser.close()


def main():
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()
    ARTIFACTS.mkdir(exist_ok=True)
    processes=[]
    logs=[]
    try:
        for module in ['server','web_demo_server']:
            handle=(ARTIFACTS/f'{module}.log').open('w')
            logs.append(handle)
            processes.append(subprocess.Popen([sys.executable,f'{module}.py'],cwd=ROOT,
                env={**os.environ,'SERVICE_MODE':'trendweave','REPORT_DB_PATH':str(ARTIFACTS/'browser-jobs.db')},
                stdout=handle,stderr=handle))
        with httpx.Client() as client:
            for _ in range(120):
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError('Server failed; see .artifacts logs')
                try:
                    if client.get('http://127.0.0.1:8600/health').json().get('analysis_ready'):
                        break
                except (httpx.HTTPError,ValueError):
                    pass
                time.sleep(.25)
            else:
                raise RuntimeError('Server unavailable; check API keys and ports')
        asyncio.run(verify())
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
        for handle in logs:
            handle.close()

if __name__=='__main__':
    main()
