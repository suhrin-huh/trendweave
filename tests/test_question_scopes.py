"""Independent brand, wearing season and collection scope across API and graph."""
import io
import pytest
from fastapi.testclient import TestClient
from agent.trendweave import engine as e
from agent.trendweave.review import default_runway_season, suggest_plan, PlanningDraft
from services.report_service import ReportService
from test_reports import report_app
from test_selective_research import approve
from conftest import Model


@pytest.mark.parametrize('at,expected',[
    ('2026-10-21T10:00:00+09:00','2027 S/S'),
    ('2026-12-31T23:59:59+09:00','2027 S/S'),
    ('2027-01-01T00:00:00+09:00','2027 S/S'),
    ('2027-02-28T23:59:59+09:00','2027 S/S'),
    ('2027-03-01T00:00:00+09:00','2027 F/W'),
    ('2027-08-31T23:59:59+09:00','2027 F/W'),
    ('2027-09-01T00:00:00+09:00','2028 S/S'),
    ('2026-08-31T16:00:00+00:00','2027 S/S'),
])
def test_runway_calendar(at,expected):
    assert default_runway_season(at)==expected


@pytest.mark.parametrize('question,kind,axes,wearing',[
    ('요즘 트렌드 분석해서 데님 코디 분석해줘','styling',['color','market'],None),
    ('여름 출근용 데님 코디 추천','styling',['color','market'],'여름'),
    ('다음 시즌 데님 상품기획 방향을 제안해줘','product_strategy',['runway','color','market'],None),
])
def test_unbranded_questions_are_supported_without_injected_brand(report_app,monkeypatch,question,kind,axes,wearing):
    monkeypatch.setattr('services.report_routes.request_time',lambda:'2026-10-21T10:00:00+09:00')
    with TestClient(report_app) as client:
        value=client.post('/reports/preview',json={'planning_request':question}).json()
        assert value['status']=='pending' and value['brand'] is None
        assert value['brand_scope']=='unspecified' and value['report_type']==kind
        assert value['selected_axes']==axes and value['wearing_season']==wearing
        assert value['runway_season']==('2027 S/S' if 'runway' in axes else None)
        assert value['runway_season_source']==('calendar' if 'runway' in axes else 'not_selected')
        report_app.state.report_service.runner=ReportService.run_graph
        response=client.post('/reports',json=approve(value))
        assert response.status_code==202
        stored=report_app.state.report_store.get(response.json()['report_id'])['request']
        assert stored['brand'] is None and stored['wearing_season']==wearing


@pytest.mark.parametrize('question,axes',[
    ('여름 출근용 데님 코디','color market'.split()),
    ('일반 데님 상품기획','runway color market'.split()),
])
def test_graph_and_markdown_keep_scopes_separate(configured,question,axes):
    result=ReportService.run_graph({'planning_request':question,'report_type':'styling' if '코디' in question else 'product_strategy',
        'selected_axes':axes,'brand':None,'wearing_season':'여름','requested_at':'2026-10-21T10:00:00+09:00'},lambda event:None)
    assert result['brand'] is None and result['research_status']['brand']=='not_selected'
    assert 'ZARA' not in result['final_markdown']
    assert '브랜드 범위: 제한 없음' in result['final_markdown'] and '착용 계절: 여름' in result['final_markdown']
    for axis,plan in result['research_plan'].items():
        assert all('summer' in query.lower() for query in plan['queries'])
        if axis!='runway':assert all('2027' not in query and 'SS27' not in query for query in plan['queries'])
    if 'runway' not in axes:
        assert result['runway_season'] is None and '런웨이 시즌:' not in result['final_markdown']
        assert result['runway_season_source']=='not_selected'
    else:
        assert result['runway_season']=='2027 S/S'
        assert result['runway_season_source']=='calendar'
        assert len(result['report']['product_directions'])==3
    assert set(result['research_results'])==set(axes)  # No wearing-season or climate research branch.


@pytest.mark.parametrize('question',['2025 F/W 런웨이를 반영한 데님 상품기획','2025 F/W로 런웨이를 조사해서 데님 상품기획'])
def test_explicit_old_collection_precedes_calendar_and_only_runway_gets_it(configured,question):
    plan=suggest_plan(question,'2026-10-21T10:00:00+09:00',model=Model())
    assert plan.runway_season=='2025 F/W' and plan.runway_season_source=='explicit'
    state=plan.model_dump() | {'planning_request':'데님 상품기획','requested_at':'2026-10-21T10:00:00+09:00'}
    state.update(configured.input_normalizer(state));state.update(configured.research_planner(state))
    for send in configured.dispatch_research(state):
        assert send.arg['season']==('2025 F/W' if send.arg['axis']=='runway' else None)
        assert bool(send.arg['season_terms'])==(send.arg['axis']=='runway')


def test_model_invented_collection_does_not_override_calendar():
    class WrongDate(Model):
        def respond(self,schema,text):
            value=super().respond(schema,text)
            if schema is PlanningDraft:value.runway_season='2026 F/W'
            return value
    plan=suggest_plan('일반 데님 상품기획','2026-10-21T10:00:00+09:00',model=WrongDate())
    assert plan.runway_season=='2027 S/S' and plan.runway_season_source=='calendar'


def test_brand_research_requires_explicit_target_and_repreview(report_app):
    with TestClient(report_app) as client:
        request={'planning_request':'브랜드 특성에 맞는 상품기획'}
        blocked=client.post('/reports/preview',json=request).json()
        assert blocked['status']=='needs_clarification' and 'preview_id' not in blocked
        general=client.post('/reports/preview',json={'planning_request':'일반 데님 상품기획'}).json()
        assert client.post('/reports',json=approve(general,brand='ZARA')).status_code==409
        specified=client.post('/reports/preview',json={**request,'brand':'ZARA','selected_axes':['runway','color','market','brand']}).json()
        assert specified['brand']=='ZARA' and specified['brand_scope']=='specific'
        assert client.post('/reports',json=approve(specified)).status_code==202


def test_llm_cannot_invent_a_default_brand():
    class DefaultBrand(Model):
        def respond(self,schema,text):
            value=super().respond(schema,text)
            if schema is PlanningDraft:
                value.brand='ZARA';value.brand_scope='specific'
            return value
    value=suggest_plan('데님 코디 추천','2026-10-21T10:00:00+09:00',model=DefaultBrand())
    assert value.brand is None and value.brand_scope=='unspecified'


def test_explicit_brand_context_does_not_force_brand_research():
    value=suggest_plan('ZARA 데님 코디 추천','2026-10-21T10:00:00+09:00',model=Model())
    assert value.brand=='ZARA' and value.selected_axes==['color','market'] and value.runway_season is None


@pytest.mark.parametrize('axis,expected',[('runway',False),('color',True),('market',True),('brand',True)])
def test_only_runway_rejects_collection_mismatch(configured,axis,expected):
    class Grade:
        def invoke(self,_):
            return e.GradeResult(grades=[e.DocumentGrade(doc_id='C1',is_relevant=True,season_match='불일치',covered_aspects=[],reason='다른 컬렉션')])
    configured.grade_model=Grade()
    state={'axis':axis,'season':'2027 S/S' if axis=='runway' else None,'season_terms':[],
        'brand':'ZARA' if axis=='brand' else None,'today':'2026-10-21','plan':{'required_aspects':[]},'new_doc_ids':['C1'],
        'candidates':[{'doc_id':'C1','title':'다른 컬렉션의 자료','content':'blue','url':'https://example.com','published_date':''}]}
    grade=configured.research_grade(state)['grades']['C1']
    assert configured.is_valid_grade(grade)==expected


def test_rewrite_without_runway_handles_empty_season_and_keeps_constraints(configured):
    class Repeated:
        def invoke(self,_):return e.RewriteResult(query='same',reason='누락 측면 확인')
    configured.rewrite_model=Repeated()
    state={'axis':'color','season':None,'season_terms':[],'brand':None,'wearing_season':'여름','time_scope':None,
        'today':'2026-10-21','candidates':[],'grades':{},'queries_tried':['same'],'missing_aspects':[]}
    query=configured.research_rewrite(state)['pending_queries'][0]
    assert '2026' in query and 'summer' in query and '2027' not in query
    assert '2027' in configured.scoped_query('2027 fashion color trends','color',{'today':'2026-10-21','time_scope':'2027년 컬러 전망'})


@pytest.mark.browser
def test_brand_neutral_product_pdf(configured):
    import asyncio
    from pypdf import PdfReader
    from services.pdf_service import create_pdf
    result=ReportService.run_graph({'planning_request':'일반 데님 상품기획','brand':None,'report_type':'product_strategy',
        'selected_axes':['runway','color','market'],'requested_at':'2026-10-21T10:00:00+09:00'},lambda event:None)
    pdf=asyncio.run(create_pdf(result['final_markdown']))
    text='\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    assert '상품기획 전략 보고서' in text and '브랜드 범위: 제한 없음' in text
    assert '2027 S/S' in text and 'ZARA' not in text
