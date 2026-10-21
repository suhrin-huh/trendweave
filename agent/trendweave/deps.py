"""Initialize the notebook's Gemini and Tavily dependencies once."""
import os
from langchain_core.rate_limiters import InMemoryRateLimiter
from . import engine


def initialize():
    missing = [key for key in ('GOOGLE_API_KEY', 'TAVILY_API_KEY') if not os.getenv(key, '').strip()]
    if missing:
        return '필요한 API 키를 .env에 설정하세요: ' + ', '.join(missing)
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_tavily import TavilySearch
        limiter = InMemoryRateLimiter(
            requests_per_second=float(os.getenv('TREND_REQUESTS_PER_SECOND', '0.25')),
            check_every_n_seconds=0.1, max_bucket_size=1)
        options = dict(model=os.getenv('TREND_MODEL', 'gemini-3.1-flash-lite'),
                       timeout=60, max_retries=2, rate_limiter=limiter)
        primary = ChatGoogleGenerativeAI(**options, temperature=0.2)
        judge = ChatGoogleGenerativeAI(**options, temperature=0)
        engine.configure(primary, judge, TavilySearch(max_results=5, search_depth='basic'))
        return None
    except Exception:
        return '분석 도구 초기화에 실패했습니다. 모델 설정과 설치된 패키지를 확인하세요.'
