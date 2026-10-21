"""Opt-in real Gemini/Tavily verification; reports saved only under .artifacts."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from agent.trendweave.deps import initialize
from agent.trendweave.review import request_time, suggest_plan
from services.report_service import ReportService


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=['non_fashion','styling','trend_analysis','product_strategy','general_product'],action='append')
    args=parser.parse_args()
    load_dotenv('.env')
    error = initialize()
    if error:
        raise SystemExit(error)
    folder = Path('.artifacts/selective-live')
    folder.mkdir(parents=True, exist_ok=True)
    summaries = []
    cases = [
        ('non_fashion', '파이썬으로 계산기 코드를 작성해줘.', 'out_of_scope'),
        ('styling', '요즘 트렌드를 분석해서 여름 출근용 데님 코디를 추천해줘. 컬러 조합과 현재 시장 스타일을 중심으로 알려줘.', None),
        ('trend_analysis', '요즘 의류 컬러 트렌드만 분석해줘. 다른 분야 조사는 필요 없어.', None),
        ('general_product', '브랜드 제한 없이 일상용 데님 상품기획 방향 3개를 제안해줘.', None),
        ('product_strategy', '2027 S/S ZARA의 가벼운 데님 상품기획 방향 3개를 제안해줘.', None),
    ]
    for name, question, blocked in cases:
        if args.case and name not in args.case:continue
        started = time.monotonic()
        at = request_time()
        value = suggest_plan(question, at)
        if blocked:
            assert isinstance(value, dict) and value['status'] == blocked
            summary = {'case': name, 'status': value['status'], 'research_started': False}
        else:
            assert not isinstance(value, dict), 'Expected a supported fashion proposal'
            plan = value.model_dump()
            assert plan['report_type'] == ('product_strategy' if name=='general_product' else name)
            if name == 'styling':
                assert set(plan['selected_axes']) == {'color', 'market'}
            if name == 'trend_analysis':
                assert plan['selected_axes'] == ['color']
            if name in ('styling','general_product','trend_analysis'):assert plan['brand'] is None
            if name in ('styling','trend_analysis'):assert plan['runway_season'] is None
            print(json.dumps({'case':name,'stage':'approved_plan','selected_axes':plan['selected_axes'],'brand':plan['brand'],'runway_season':plan['runway_season']},ensure_ascii=False),flush=True)
            request = {**plan, 'planning_request': question, 'requested_at': at}
            events = []
            final = ReportService.run_graph(request, events.append)
            assert set(final['research_results']) == set(plan['selected_axes'])
            assert sum(event['step'] == 'strategy' for event in events) == final['revision_count'] + 1
            assert final['final_markdown'] and final['report']['executive_summary']
            (folder / f'{name}.md').write_text(final['final_markdown'])
            (folder / f'{name}.json').write_text(json.dumps(final, ensure_ascii=False, indent=2))
            summary = {'case': name, 'report_type': final['report_type'], 'selected_axes': plan['selected_axes'],
                       'quality_status': final['quality_status'], 'research_status': final['research_status'],
                       'revision_count': final['revision_count']}
        summary['elapsed_seconds'] = round(time.monotonic() - started, 1)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        (folder / 'summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Provider exceptions can contain credentials and URLs; keep output generic.
        raise SystemExit(f'Live verification failed ({type(error).__name__}). Check local configuration and provider limits.') from None
