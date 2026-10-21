"""Bounded Tavily HTTP adapter matching the notebook's invoke interface."""
import httpx


class TavilySearchAdapter:
    def __init__(self, api_key, timeout=30):
        self.api_key = api_key
        self.timeout = httpx.Timeout(timeout, connect=min(10, timeout))

    def invoke(self, request):
        # langchain-tavily's synchronous wrapper has no request timeout. Keep
        # the same search input/result contract with a bounded HTTP request.
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post('https://api.tavily.com/search',
                headers={'Authorization': f'Bearer {self.api_key}'},
                json={'max_results': 5, 'search_depth': 'basic', **request})
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict):
            raise ValueError('Tavily returned an invalid search result')
        return result
