"""Shared sanitized Markdown renderer and Korean PDF export."""
import base64
import re
from pathlib import Path
import bleach
import markdown

ROOT = Path(__file__).resolve().parent.parent
TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'hr', 'br',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'details', 'summary', 'div', 'span'}


def render_markdown(source):
    # Remove executable/raw active blocks, then allow only passive report markup.
    source = re.sub(r'<(script|style|iframe)\b[^>]*>.*?</\1\s*>', '', source,
                    flags=re.I | re.S)
    html = markdown.markdown(source, extensions=['tables', 'fenced_code', 'md_in_html'])
    return bleach.clean(html, tags=TAGS, attributes={'a': ['href', 'title'],
                        'details': ['open']}, protocols=['https', 'http', 'mailto'], strip=True)


def pdf_html(source):
    font = base64.b64encode((ROOT / 'static/fonts/NotoSansCJKkr-Regular.otf').read_bytes()).decode()
    body = render_markdown(source)
    body = re.sub(r'<details(?:\s[^>]*)?>', '<details open>', body)
    template = (ROOT / 'templates/report_pdf.html').read_text()
    return template.replace('{{FONT}}', font).replace('{{BODY}}', body)


async def create_pdf(source):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            # A report never needs to fetch external assets to print.
            await page.route('**/*', lambda route: route.abort())
            await page.set_content(pdf_html(source), wait_until='load', timeout=30000)
            await page.evaluate('document.fonts.ready')
            return await page.pdf(
                format='A4', print_background=True, display_header_footer=True,
                margin={'top': '16mm', 'bottom': '20mm', 'left': '15mm', 'right': '15mm'},
                header_template='<span></span>',
                footer_template='<div style="font-size:9px;width:100%;text-align:center;color:#777">TrendWeave · <span class="pageNumber"></span> / <span class="totalPages"></span></div>')
        finally:
            await browser.close()
