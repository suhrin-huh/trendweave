"""Review must happen before report submission, including at the API boundary."""
import json
from fastapi.testclient import TestClient
import pytest
from agent.trendweave.review import PlanningSuggestion, DomainDecision, suggest_plan
from services.report_store import ReportStore
from services.report_service import ReportService
from test_reports import report_app


def proposal(client, message='ZARA 한국 20-30대의 가벼운 여름 데님 상품을 기획해줘.', thread='review-test'):
    response=client.post('/reports/preview',json={'planning_request':message,'thread_id':thread})
    assert response.status_code==200,response.text
    return response.json()


def approval(value, **changes):
    return {'preview_id':value['preview_id'],'approved':True,'brand':value['brand'],
            'runway_season':value['runway_season'],'planning_request':value['planning_request'],
            'report_direction':value['report_direction'],'thread_id':'review-test','report_type':value['report_type'],'selected_axes':value['selected_axes'],**changes}


def job_count(app):
    return app.state.report_store.conn.execute('SELECT COUNT(*) FROM report_jobs').fetchone()[0]


def test_preview_uses_server_time_without_running_report(report_app,monkeypatch):
    at='2026-12-31T23:59:59+09:00'
    monkeypatch.setattr('services.report_routes.request_time',lambda:at)
    with TestClient(report_app) as client:
        captured=[]
        def propose(message,requested_at):
            captured.append((message,requested_at))
            return PlanningSuggestion(runway_season='2027 ss',brand='ZARA',report_direction='데님 상품 전략',reason='요청 시점의 다음 여름을 준비한다.')
        report_app.state.preview_service.proposer=propose
        value=proposal(client)
        assert value['runway_season']=='2027 S/S' and value['brand']=='ZARA'
        assert value['requested_at']==at and value['status']=='pending'
        assert captured==[(value['planning_request'],at)]
        assert job_count(report_app)==0
        stored=report_app.state.report_store.get_preview(value['preview_id'])
        assert stored['status']=='pending' and stored['report_id'] is None
        assert client.post('/reports/preview',json={'planning_request':'기획','requested_at':'2000-01-01'}).status_code==422


def test_prompt_supplies_date_question_and_explicit_season():
    captured=[]
    class Model:
        def with_structured_output(self,schema):
            self.schema=schema
            return self
        def invoke(self,messages):
            if self.schema is DomainDecision:
                return DomainDecision(domain_status='fashion',reason='패션 상품기획',supported=True)
            captured.extend(messages)
            return PlanningSuggestion(runway_season='2028 F/W',brand='ZARA',report_direction='명시된 시즌 분석',reason='사용자가 지정한 시즌을 따른다.')
    result=suggest_plan('2028 F/W 시즌 상품기획','2026-10-21T14:30:00+09:00',model=Model())
    data=json.loads(captured[1].content)
    assert data['requested_at']=='2026-10-21T14:30:00+09:00'
    assert data['planning_request']=='2028 F/W 시즌 상품기획'
    assert result.runway_season=='2028 F/W'
    assert '최종 생성 여부는 사용자가' in captured[0].content


def test_approval_gate_and_user_edits(report_app):
    with TestClient(report_app) as client:
        value=proposal(client)
        for changes in [{'approved':False},{'brand':'UNIQLO'},{'runway_season':'내년'}, {'report_direction':'   '}]:
            assert client.post('/reports',json=approval(value,**changes)).status_code==422
        payload=approval(value)
        payload.pop('approved')
        assert client.post('/reports',json=payload).status_code==422
        assert client.post('/reports',json=approval(value,preview_id='unknown')).status_code==404
        assert client.post('/reports',json=approval(value,thread_id='other-conversation')).status_code==404
        assert job_count(report_app)==0
        payload=approval(value,runway_season='2028 FW',planning_request='ZARA 가벼운 데님 상품만 기획해줘.',report_direction='여름용 데님 소재와 아이템을 중점 검토한다.')
        response=client.post('/reports',json=payload)
        assert response.status_code==409
        revised=client.post('/reports/preview',json={'thread_id':'review-test',
            'planning_request':payload['planning_request'],'runway_season':payload['runway_season'],
            'report_direction':payload['report_direction']}).json()
        response=client.post('/reports',json=approval(revised))
        assert response.status_code==202
        report_id=response.json()['report_id']
        request=report_app.state.report_store.get(report_id)['request']
        assert request['runway_season']=='2028 F/W'
        assert request['planning_request']==payload['planning_request']
        assert request['report_direction']==payload['report_direction']
        assert request['approved'] is True
        assert request['requested_at']==revised['requested_at']
        assert report_app.state.report_store.get_preview(revised['preview_id'])['status']=='approved'
        assert report_app.state.report_store.get_preview(value['preview_id'])['status']=='pending'


def test_duplicate_approval_does_not_start_twice(report_app):
    with TestClient(report_app) as client:
        value=proposal(client)
        payload=approval(value)
        first=client.post('/reports',json=payload)
        again=client.post('/reports',json=payload)
        assert first.status_code==again.status_code==202
        assert first.json()['report_id']==again.json()['report_id']
        assert job_count(report_app)==1
        assert client.post('/reports',json=approval(value,runway_season='2029 F/W')).status_code==409
        assert client.post(f'/reports/preview/{value["preview_id"]}/cancel',json={'thread_id':'review-test'}).status_code==409


def test_cancel_creates_no_report(report_app):
    with TestClient(report_app) as client:
        value=proposal(client)
        path=f'/reports/preview/{value["preview_id"]}/cancel'
        assert client.post(path,json={'thread_id':'other'}).status_code==404
        assert client.post(path,json={'thread_id':'review-test'}).status_code==200
        assert client.post(path,json={'thread_id':'review-test'}).status_code==200
        assert client.post('/reports',json=approval(value)).status_code==409
        assert job_count(report_app)==0


def test_model_failure_never_creates_report(report_app):
    with TestClient(report_app) as client:
        def fail(*_):
            raise RuntimeError('private provider detail')
        report_app.state.preview_service.proposer=fail
        response=client.post('/reports/preview',json={'planning_request':'상품 기획'})
        assert response.status_code==503 and '보고서는 생성하지 않았습니다' in response.text
        assert 'private provider detail' not in response.text
        assert job_count(report_app)==0
        assert report_app.state.report_store.conn.execute('SELECT COUNT(*) FROM report_previews').fetchone()[0]==0


def test_queue_full_keeps_review_pending(report_app,monkeypatch):
    with TestClient(report_app) as client:
        value=proposal(client)
        def full(*_,**__):
            raise OverflowError('queue full')
        monkeypatch.setattr(report_app.state.report_service,'submit',full)
        assert client.post('/reports',json=approval(value)).status_code==429
        assert report_app.state.report_store.get_preview(value['preview_id'])['status']=='pending'
        assert job_count(report_app)==0


def test_reviewed_direction_reaches_graph(configured):
    result=ReportService.run_graph({'runway_season':'2027 S/S','planning_request':'일상 상품 기획',
                                   'report_direction':'데님 중심으로 기획한다.'},lambda event:None)
    assert '보고서 방향: 데님 중심으로 기획한다.' in result['planning_request']
    assert 'denim' in result['research_plan']['runway']['queries'][0]


def test_preview_audit_survives_restart(tmp_path):
    path=tmp_path/'reports.db'
    store=ReportStore(path)
    value=store.create_preview('review-test',{'requested_at':'2026-10-21T10:30:00+09:00','runway_season':'2027 S/S','brand':'ZARA'})
    store.close()
    store=ReportStore(path)
    assert store.get_preview(value['preview_id'])['status']=='pending'
    assert store.get_preview(value['preview_id'])['payload']['requested_at']=='2026-10-21T10:30:00+09:00'
    store.close()


@pytest.mark.parametrize('metadata',[
    {},
    {'schema_version':1,'domain_status':'pass','domain_reason':'모델이 만든 이유'},
    {'schema_version':999,'domain_status':'non_fashion','domain_reason':'임의 변경'},
])
def test_planning_parser_ignores_llm_metadata_and_server_stamps_envelope(report_app,metadata):
    from langchain_core.output_parsers import PydanticOutputParser
    from agent.trendweave.review import PlanningDraft
    schemas=[]
    class ParsedModel:
        def with_structured_output(self,schema):
            schemas.append(schema)
            parser=PydanticOutputParser(pydantic_object=schema)
            class Structured:
                def invoke(self,messages):
                    payload=({'domain_status':'fashion','supported':True,'reason':'서버가 보존할 선행 판정'}
                             if schema is DomainDecision else {
                                 'runway_season':'2026 F/W','brand':'ZARA','report_type':'product_strategy',
                                 'selected_axes':['runway','color','market','brand'],
                                 'report_direction':'한국 20-30대의 일상 상품기획 방향 3개',
                                 'reason':'요청 시점과 질문을 기준으로 제안',**metadata})
                    return parser.parse(json.dumps(payload,ensure_ascii=False))
            return Structured()
    with TestClient(report_app) as client:
        report_app.state.preview_service.proposer=lambda question,at: suggest_plan(question,at,model=ParsedModel())
        response=client.post('/reports/preview',json={'planning_request':'한국 20-30대 ZARA 일상 상품기획'})
        assert response.status_code==200,response.text
        value=response.json()
        assert value['status']=='pending'
        assert value['schema_version']==3 and value['domain_status']=='fashion'
        assert value['domain_reason']=='서버가 보존할 선행 판정'
        assert schemas==[DomainDecision,PlanningDraft]
        assert not {'schema_version','domain_status','domain_reason'} & PlanningDraft.model_json_schema()['properties'].keys()
        saved=report_app.state.report_store.get_preview(value['preview_id'])['payload']
        assert saved['schema_version']==3 and saved['domain_status']=='fashion'
        assert job_count(report_app)==0


def test_final_suggestion_remains_strict_and_invalid_plans_are_rejected():
    from pydantic import ValidationError
    from agent.trendweave.review import PlanningDraft
    valid={'runway_season':'2027 S/S','brand':'ZARA','report_direction':'상품기획','reason':'명시 시즌'}
    for metadata in [{'schema_version':1},{'domain_status':'pass'}]:
        with pytest.raises(ValidationError):
            PlanningSuggestion(**valid,**metadata)
    for changes in [{'selected_axes':[]},{'report_type':'unknown'},{'runway_season':'잘못된 시즌'}]:
        with pytest.raises(ValidationError):
            PlanningDraft(**{**valid,**changes})


def test_brand_research_without_target_requests_clarification(report_app):
    question='한국의 20-30대 고객이 일상에서 활용하기 쉬운 상품 중심으로 런웨이·컬러·시장·브랜드 트렌드를 분석하고 상품기획 방향 3개를 제안해줘.'
    with TestClient(report_app) as client:
        value=proposal(client,question)
        assert value['status']=='needs_clarification' and 'preview_id' not in value
        assert '브랜드 조사 대상' in value['message']
        assert job_count(report_app)==0
