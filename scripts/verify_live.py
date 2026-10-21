"""Explicit live integration check. Uses real configured Gemini/Tavily APIs."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from agent.trendweave.deps import initialize
from services.report_service import ReportService


def main():
    error = initialize()
    if error:
        raise SystemExit(error)
    def emit(event):
        # Print only node/axis names, never raw provider exceptions or secrets.
        print(event['step'], event.get('axis',''), flush=True)
    output = ReportService.run_graph({'season':'2027 S/S','planning_request':
        '한국의 20-30대 고객이 일상에서 활용하기 쉬운 상품 중심으로 기획해줘.',
        'thread_id':'live-verification'}, emit)
    directory=Path('.artifacts')
    directory.mkdir(exist_ok=True)
    (directory/'live-report.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
    (directory/'live-report.md').write_text(output['final_markdown'])
    print(json.dumps({'axes':list(output['research_results']),
        'directions':len(output['report']['product_directions']),
        'revisions':output['revision_count'],
        'critic_passed':output['critique']['passed'],
        'llm_reviewed':output['critique']['llm_reviewed'],
        'quality':{key:value['confidence'] for key,value in output['research_results'].items()}},ensure_ascii=False))

if __name__ == '__main__':
    main()
