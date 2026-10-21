"""Domain gate, selected branches, typed reports and approval invariants."""
import io
import time
import pytest
from fastapi.testclient import TestClient
from agent.trendweave import engine as e
from agent.trendweave.review import DomainDecision, PlanningSuggestion, suggest_plan, OUT_OF_SCOPE
from services.report_service import ReportService
from services.pdf_service import render_markdown
from test_reports import report_app
from conftest import Model, Search


def approve(value, **changes):
    return {key:value[key] for key in ['preview_id','planning_request','season','brand','report_direction','report_type','selected_axes']} | {'approved':True,**changes}


@pytest.mark.parametrize('question,status',[
    ('파이썬 코드를 작성해줘','out_of_scope'),('패션 브랜드 주가 예측','out_of_scope'),
    ('추천해줘','needs_clarification'),('패션과 비패션 혼합 요청','needs_clarification'),
    ('ZARA와 H&M 비교','unsupported')])
def test_domain_rejections_never_create_preview_or_job(report_app,question,status):
    with TestClient(report_app) as client:
        value=client.post('/reports/preview',json={'planning_request':question}).json()
        assert value['status']==status and 'preview_id' not in value
        assert 'report_type' not in value
        if status=='out_of_scope':assert value['message']==OUT_OF_SCOPE
        store=report_app.state.report_store
        assert store.conn.execute('SELECT COUNT(*) FROM report_previews').fetchone()[0]==0
        assert store.conn.execute('SELECT COUNT(*) FROM report_jobs').fetchone()[0]==0


def test_non_fashion_does_not_call_planning_model():
    class GateOnly:
        def with_structured_output(self,schema):
            assert schema is DomainDecision
            return self
        def invoke(self,messages):
            return DomainDecision(domain_status='non_fashion',reason='비패션',supported=False)
    assert suggest_plan('수학 문제','2026-10-21T10:00:00+09:00',model=GateOnly())['status']=='out_of_scope'


@pytest.mark.parametrize('kind,axes,question',[
    ('styling',['color','market'],'여름 출근용 데님 코디'),
    ('trend_analysis',['color'],'2027 S/S 의류 컬러 트렌드')])
def test_only_selected_branches_run_and_report_is_written_once(configured,kind,axes,question):
    events=[]
    result=ReportService.run_graph({'season':'2027 S/S','brand':None,'report_type':kind,
        'selected_axes':axes,'planning_request':question,'requested_at':'2026-10-21T10:30:00+09:00'}, events.append)
    assert set(result['research_plan'])==set(axes)
    assert set(result['research_results'])==set(axes)
    assert {event['axis'] for event in events if event.get('axis')}==set(axes)
    assert len([event for event in events if event['step']=='strategy'])==1
    assert len(configured.search_tool.calls)==len(axes)
    assert result['brand'] is None and result['today']=='2026-10-21'
    assert result['quality_status']=='passed'
    assert result['research_status']['brand']=='not_selected'
    assert result['critique']['passed']
    assert '<table>' in render_markdown(result['final_markdown'])
    assert ('코디 제안' if kind=='styling' else '신호 해석과 적용') in result['final_markdown']
    assert 'ZARA' not in result['final_markdown']
    assert 'outfits' in result['report'] if kind=='styling' else 'findings' in result['report']


def test_selection_constraints_and_stale_review(report_app):
    with TestClient(report_app) as client:
        value=client.post('/reports/preview',json={'planning_request':'데님 코디 추천'}).json()
        assert value['report_type']=='styling' and value['brand'] is None
        for axes in [[],['color'],['color','color','market']]:
            assert client.post('/reports',json=approve(value,selected_axes=axes)).status_code==422
        for changes in [{'planning_request':'파이썬 코드 작성'}, {'report_direction':'맛집 추천'}, {'season':'2028 F/W'}]:
            assert client.post('/reports',json=approve(value,**changes)).status_code==409
        assert client.post('/reports',json=approve(value,domain_status='fashion')).status_code==422
        response=client.post('/reports',json=approve(value,report_type='trend_analysis',selected_axes=['color']))
        assert response.status_code==202
        stored=report_app.state.report_store.get(response.json()['report_id'])['request']
        assert stored['selected_axes']==['color'] and stored['domain_status']=='fashion'
        assert client.post('/reports',json=approve(value,report_type='trend_analysis',selected_axes=['color'])).json()['report_id']==response.json()['report_id']
        assert client.post('/reports',json=approve(value)).status_code==409


def test_direction_edit_reclassified_before_plan(report_app):
    with TestClient(report_app) as client:
        response=client.post('/reports/preview',json={'planning_request':'코디 추천',
            'season':'2027 S/S','report_direction':'파이썬 코드 작성'})
        assert response.status_code==200
        assert response.json()['status']=='out_of_scope'


def test_all_selected_research_empty_fails(configured):
    configured.search_tool=Search(empty=True)
    with pytest.raises(RuntimeError,match='유효한 근거'):
        ReportService.run_graph({'season':'2027 S/S','brand':None,'report_type':'trend_analysis',
            'selected_axes':['color'],'planning_request':'의류 컬러 트렌드'},lambda event:None)


def test_missing_branch_and_invalid_evidence_are_visible(configured,monkeypatch):
    from agent.trendweave.dynamic_reports import rule_check
    class PartialModel(Model):
        def respond(self,schema,text):
            if schema is e.ColorSignal:
                raise RuntimeError('private failure')
            if schema is e.StylingReport:
                result=super().respond(schema,text)
                for outfit in result.outfits:outfit.evidence_ids=['M1']
                result.limitations=['컬러 조사를 완료하지 못함']
                return result
            return super().respond(schema,text)
    configured.configure(PartialModel(),Model(),Search())
    result=ReportService.run_graph({'season':'2027 S/S','brand':None,'report_type':'styling',
        'selected_axes':['color','market'],'planning_request':'데님 코디'},lambda event:None)
    assert result['research_status']['color']=='failed'
    assert result['research_status']['runway']=='not_selected'
    assert result['quality_status']=='limited'
    assert 'private failure' not in result['final_markdown']
    report=e.StylingReport.model_validate(result['report'])
    report.outfits[0].evidence_ids=['R999']
    state={'report':report,'research_results':{axis:e.AxisResult.model_validate(value) for axis,value in result['research_results'].items()}}
    assert any(issue.type=='grounding' for issue in rule_check(state))


@pytest.mark.browser
@pytest.mark.parametrize('kind,axes',[('styling',['color','market']),('trend_analysis',['color'])])
def test_new_report_korean_pdf(configured,kind,axes):
    import asyncio
    from services.pdf_service import create_pdf
    from pypdf import PdfReader
    result=ReportService.run_graph({'season':'2027 S/S','brand':None,'report_type':kind,
        'selected_axes':axes,'planning_request':'패션 트렌드와 코디'},lambda event:None)
    content=asyncio.run(create_pdf(result['final_markdown']))
    text='\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)
    assert ('코디 추천서' if kind=='styling' else '패션 트렌드 분석서') in text
    assert 'References' in text and 'Agent Trace' in text


@pytest.mark.parametrize('question,kind,axes',[
    ('여름 데님 코디 추천','styling',['color','market']),
    ('2027 S/S 의류 컬러 트렌드','trend_analysis',['color'])])
def test_new_types_through_api_job_storage_and_pdf_name(report_app,monkeypatch,question,kind,axes):
    async def pdf(markdown):
        assert ('코디 추천서' if kind=='styling' else '패션 트렌드 분석서') in markdown
        return b'%PDF-test'
    monkeypatch.setattr('services.pdf_service.create_pdf',pdf)
    with TestClient(report_app) as client:
        report_app.state.report_service.runner=ReportService.run_graph
        value=client.post('/reports/preview',json={'planning_request':question}).json()
        assert value['report_type']==kind
        response=client.post('/reports',json=approve(value))
        assert response.status_code==202
        report_id=response.json()['report_id']
        for _ in range(100):
            data=client.get('/reports/'+report_id).json()
            if data['status'] in ('completed','failed'):break
            time.sleep(.01)
        assert data['status']=='completed'
        assert data['result']['report_type']==kind
        assert data['result']['selected_axes']==axes
        assert data['result']['brand'] is None
        assert data['result']['quality_status']=='passed'
        assert data['report_html'] and data['events'][-1]['step']=='completed'
        download=client.get(f'/reports/{report_id}/pdf')
        assert download.status_code==200
        assert f'TrendWeave_{kind}_' in download.headers['content-disposition']
        assert 'ZARA' not in download.headers['content-disposition']


def test_domain_provider_failure_is_not_a_rejection(report_app):
    class Broken:
        def with_structured_output(self,schema):
            assert schema is DomainDecision
            return self
        def invoke(self,_):raise RuntimeError('provider private detail')
    with TestClient(report_app) as client:
        report_app.state.preview_service.proposer=lambda question,at: suggest_plan(question,at,model=Broken())
        response=client.post('/reports/preview',json={'planning_request':'코디 추천'})
        assert response.status_code==503
        assert 'provider private detail' not in response.text
        assert report_app.state.report_store.conn.execute('SELECT COUNT(*) FROM report_jobs').fetchone()[0]==0


def test_search_error_is_failed_instead_of_unselected(configured,monkeypatch):
    monkeypatch.setattr(configured,'safe_search',lambda *args:([], 'private search failure'))
    state={'season':'2027 S/S','brand':None,'report_type':'trend_analysis',
        'selected_axes':['color'],'planning_request':'의류 컬러 트렌드'}
    state.update(configured.input_normalizer(state))
    state.update(configured.research_planner(state))
    output=configured.research_subgraph.invoke(configured.dispatch_research(state)[0].arg)
    assert output['result'].status=='failed'
    assert 'private search failure' not in str(output)


@pytest.mark.parametrize('failure',['grounding','critic_unavailable'])
def test_new_reports_show_remaining_review_issues(configured,failure):
    class BadModel(Model):
        def respond(self,schema,text):
            value=super().respond(schema,text)
            if schema is e.TrendAnalysisReport:
                value.findings[0].evidence_ids=['C999']
            return value
    class BadCritic(Model):
        def respond(self,schema,text):
            if schema is e.CriticReview:raise RuntimeError('provider unavailable')
            return super().respond(schema,text)
    configured.configure(BadModel() if failure=='grounding' else Model(),
                         BadCritic() if failure=='critic_unavailable' else Model(), Search())
    result=ReportService.run_graph({'season':'2027 S/S','brand':None,'report_type':'trend_analysis',
        'selected_axes':['color'],'planning_request':'의류 컬러 트렌드'},lambda event:None)
    assert result['quality_status']=='review_required'
    if failure=='grounding':
        assert result['revision_count']==2 and not result['critique']['passed']
    else:
        assert result['revision_count']==0 and not result['critique']['llm_reviewed']
        assert '규칙 검사만 적용' in result['final_markdown']
