"""Real LLM scope proposals; no web search or report generation."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from agent.trendweave.deps import initialize
from agent.trendweave.review import request_time, suggest_plan, default_runway_season

CASES={
    'styling':'요즘 트렌드 분석해서 여름 출근용 데님 코디를 추천해줘.',
    'general_product':'브랜드 제한 없이 일상용 데님 상품기획 방향 3개를 제안해줘.',
    'brand_missing':'런웨이·컬러·시장·브랜드 트렌드를 분석하고 상품기획 방향 3개를 제안해줘.',
    'explicit':'2028 F/W 런웨이를 반영하여 ZARA 상품기획 방향을 제안해줘.',
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=CASES)
    args=parser.parse_args()
    load_dotenv('.env')
    error=initialize()
    if error:raise SystemExit(error)
    at=request_time();results=[]
    for name,message in CASES.items():
        if args.case and name!=args.case:continue
        value=suggest_plan(message,at)
        if name=='brand_missing':
            assert isinstance(value,dict) and value['status']=='needs_clarification'
            result={'case':name,**value}
        else:
            assert not isinstance(value,dict),'Expected supported analysis plan'
            assert value.schema_version==3
            if name=='styling':
                assert value.brand is None and value.runway_season is None and value.wearing_season
                assert value.report_type=='styling' and set(value.selected_axes)=={'color','market'}
            elif name=='general_product':
                assert value.brand is None and 'brand' not in value.selected_axes
                assert value.report_type=='product_strategy'
                if 'runway' in value.selected_axes:assert value.runway_season==default_runway_season(at)
            else:
                assert value.brand=='ZARA' and value.runway_season=='2028 F/W'
            result={'case':name,'planning_request':message,**value.model_dump()}
        result.update(requested_at=at,report_created=False)
        results.append(result)
        print(json.dumps({key:result.get(key) for key in ('case','status','brand','runway_season','wearing_season','selected_axes','report_created')},ensure_ascii=False),flush=True)
    folder=Path('.artifacts');folder.mkdir(exist_ok=True)
    (folder/('live-preview-'+(args.case or 'all')+'.json')).write_text(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:
        raise SystemExit(f'LLM preview failed ({type(error).__name__}); check model/API configuration') from None
