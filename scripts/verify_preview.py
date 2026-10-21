"""Real LLM season suggestions only; never searches or generates reports."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from agent.trendweave.deps import initialize
from agent.trendweave.review import request_time, suggest_plan


def main():
    load_dotenv('.env')
    error=initialize()
    if error:raise SystemExit(error)
    at=request_time()
    cases=[('implicit','한국 20-30대 고객을 위한 가벼운 일상 상품을 기획해줘.'),
           ('explicit','2028 F/W 시즌 ZARA 상품기획 방향을 제안해줘.')]
    results=[]
    for name,message in cases:
        try:
            value=suggest_plan(message,at)
        except Exception as error:
            raise SystemExit(f'LLM preview failed ({type(error).__name__}); check model/API configuration')
        if name=='explicit':assert value.season=='2028 F/W'
        results.append({'case':name,'requested_at':at,'planning_request':message,**value.model_dump()})
        print(json.dumps({'case':name,'requested_at':at,'season':value.season,'brand':value.brand,
                          'report_created':False},ensure_ascii=False),flush=True)
    folder=Path('.artifacts');folder.mkdir(exist_ok=True)
    (folder/'live-preview.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
