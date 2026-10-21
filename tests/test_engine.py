import pytest
from conftest import Search


def test_full_graph_and_request_planning(configured):
    e=configured
    events=[]
    states=[]
    for mode,payload in e.trend_agent.stream({'season':'2027 S/S','brand':'UNIQLO','planning_request':'데님 상품 기획'},
            config={'recursion_limit':50},stream_mode=['custom','values']):
        (events if mode=='custom' else states).append(payload)
    result=states[-1]
    assert set(result['research_results']) == set(e.AXES)
    assert all(value.attempts == 1 for value in result['research_results'].values())
    assert 'denim' in result['research_plan']['runway']['queries'][0]
    assert len(result['report'].product_directions)==3
    assert result['critique'].passed
    assert result['revision_count']==0
    assert sum(ev['step']=='strategy' for ev in events)==1
    assert all(name in result['final_markdown'] for name in ['Data Quality','Review Notes','Agent Trace','References','Strategy 3'])
    assert {'runway','color','market','brand'} <= {ev['axis'] for ev in events}


def test_empty_research_stops_at_bound(configured):
    e=configured
    e.search_tool=Search(empty=True)
    state={'season':'2028 F/W','brand':'UNIQLO','planning_request':'상품 기획'}
    state.update(e.input_normalizer(state));state.update(e.research_planner(state))
    branch=e.research_subgraph.invoke(e.dispatch_research(state)[0].arg,config={'recursion_limit':50})
    assert branch['result'].attempts == 3
    assert branch['result'].confidence=='낮음'
    assert branch['result'].data_gaps
    with pytest.raises(RuntimeError,match='자료를 확보'):
        e.analyze_product_strategy('2028 F/W','UNIQLO','상품 기획')


def test_evidence_and_color_rules(configured,result):
    e=configured
    result['report'].product_directions[0].evidence_ids=['R1','INVALID']
    issues=e.rule_check(result)
    assert any('존재하지 않는' in issue.detail for issue in issues)
    assert any('Color 근거' in issue.detail for issue in issues)
    result['research_results']['color'].signal=e.empty_signal('color')
    for direction in result['report'].product_directions:
        direction.recommended_colors=[]
        direction.evidence_ids=['R1']
    assert not any('Color 근거' in issue.detail or '추천 컬러' in issue.detail for issue in e.rule_check(result))


def test_revisions_stop_and_critic_failure(configured,result):
    e=configured
    class BadCritic:
        def invoke(self,_):
            raise RuntimeError('provider unavailable')
    e.critic_model=BadCritic()
    critique=e.strategy_critic(result)['critique']
    assert not critique.llm_reviewed
    assert critique.passed
    class WrongWriter:
        def invoke(self,_):
            report=result['report'].model_copy(deep=True)
            report.product_directions=report.product_directions[:1]
            return report
    e.strategy_model=WrongWriter()
    output=e.analyze_product_strategy('2027 S/S','UNIQLO','상품 기획')
    assert output['revision_count']==2
    assert not output['critique'].passed
    assert '한도' in output['final_markdown']


def test_grounding_cleanup(configured):
    e=configured
    signal=e.RunwaySignal(silhouettes=[],materials=[],details=[],key_items=[
        e.Signal(name='허위',description='없는 근거',evidence_ids=['R999'])])
    assert not e.clean_evidence(signal,{'R1'}).key_items
    assert e.normalize_aspects(['소재'],['데님 소재'])==[]
    assert e.normalize_aspects(['핵심 아이템'],['아이템'])==['아이템']


def test_grade_season_and_market_rules(configured):
    e=configured
    class Grades:
        def invoke(self,_):
            return e.GradeResult(grades=[e.DocumentGrade(doc_id='R1',is_relevant=True,season_match='불일치',covered_aspects=['소재'],reason='이전 시즌')])
    e.grade_model=Grades()
    state={'axis':'runway','plan':{'required_aspects':['소재']},'season':'2027 S/S','brand':'UNIQLO',
           'today':'2026-10-21','season_terms':['SS27'], 'new_doc_ids':['R1'],
           'candidates':[{'doc_id':'R1','title':'SS26','url':'https://example.com','content':'linen','published_date':''}]}
    output=e.research_grade(state)
    assert not e.is_valid_grade(output['grades']['R1'])
    assert output['missing_aspects']==['소재']
    state['axis']='market'
    output=e.research_grade(state)
    assert e.is_valid_grade(output['grades']['R1'])
    assert output['grades']['R1']['season_match']=='해당 없음'


def test_tavily_bounded_http(monkeypatch):
    import httpx
    from agent.trendweave.search import TavilySearchAdapter
    seen=[]
    class Client:
        def __init__(self,timeout):
            assert timeout.read==7 and timeout.connect==7
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):
            seen.append(json)
            return httpx.Response(200,json={'results':[]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'Client',Client)
    assert TavilySearchAdapter('test-key',timeout=7).invoke({'query':'SS27','topic':'news'})=={'results':[]}
    assert seen[0]['max_results']==5
