import re
import json
import pytest
from agent.trendweave import engine as e
from agent.trendweave.review import PlanningDraft, DomainDecision


class Search:
    def __init__(self, empty=False):
        self.empty = empty
        self.calls = []

    def invoke(self, request):
        self.calls.append(request)
        if self.empty:
            return {'results': []}
        return {'results': [{'title': f'Spring Summer 2027 collection {i}',
                             'url': f'https://example.com/research/{i}',
                             'content': 'Linen shirts, blue palette, light materials, consumer demand and ZARA basics.'}
                            for i in range(3)]}


class Model:
    def with_structured_output(self, schema):
        parent = self
        class Structured:
            def invoke(self, messages):
                return parent.respond(schema, '\n'.join(message.content for message in messages))
        return Structured()

    def respond(self, schema, text):
        if schema is DomainDecision:
            data=json.loads(text.split('\n')[-1])
            question=data['planning_request'] + (data.get('direction_override') or '')
            if '혼합' in question or question == '추천해줘':
                return DomainDecision(domain_status='unclear',reason='범위 보완 필요',supported=False)
            if any(word in question for word in ['파이썬','맛집','주가','수학']):
                return DomainDecision(domain_status='non_fashion',reason='비패션 목적',supported=False)
            return DomainDecision(domain_status='fashion',reason='패션 분석',supported='비교' not in question)
        if schema is PlanningDraft:
            data=json.loads(text.split('\n')[-1])
            question=data['planning_request']
            if '코디' in question or '컬러 트렌드' in question:
                styling='코디' in question
                return PlanningDraft(season=data.get('season_override') or '2027 S/S',brand=None,
                    report_type='styling' if styling else 'trend_analysis',selected_axes=['color','market'] if styling else ['color'],
                    axis_reasons={'color':'컬러 근거','market':'시장 근거'} if styling else {'color':'컬러 근거'},
                    report_direction=data.get('direction_override') or question,reason='요청에 맞는 패션 분석')
            return PlanningDraft(season='2027 S/S',brand='ZARA',
                report_direction='요청에 맞춘 상품기획 방향 3개를 제안한다.',reason='다음 봄·여름 시즌을 준비한다.')
        if schema is e.SelectedResearchPlan:
            data=json.loads(text.split('\n')[-1])
            return e.SelectedResearchPlan(request_profile=e.RequestProfile(target_customer='명시되지 않음',
                focus_categories=[],constraints=[],priority='균형'),
                axes={axis:e.AxisPlan(focus_keywords=[],query='',extra_aspect='') for axis in data['selected_axes']})
        if schema is e.StylingReport:
            return e.StylingReport(executive_summary='블루 데님 코디를 제안한다.',usage_tips=['출근 상황에 맞춘다.'],limitations=[],
                outfits=[e.Outfit(name=f'코디 {i}',composition=['셔츠','데님'],color_combination=['블루'],occasion='출근',
                    rationale='컬러와 시장 신호를 연결한다.',caution='재고는 확인하지 않았다.',evidence_ids=['C1','M1']) for i in range(3)])
        if schema is e.TrendAnalysisReport:
            return e.TrendAnalysisReport(executive_summary='블루 컬러 트렌드다.',watch_points=['후속 시즌 관찰'],limitations=[],
                findings=[e.TrendFinding(name='블루',observation='자료에 블루가 있다.',interpretation='활용 가능성이 있다.',application='팔레트 검토',evidence_ids=['C1'])])
        if schema is e.ResearchPlan:
            denim = '데님' in text
            axis = e.AxisPlan(focus_keywords=['denim'] if denim else [], query='', extra_aspect='데님' if denim else '')
            return e.ResearchPlan(request_profile=e.RequestProfile(target_customer='한국 20-30대',
                focus_categories=['데님'] if denim else [], constraints=[], priority='실용성 우선'),
                **{name: axis for name in e.AXES})
        if schema is e.GradeResult:
            ids = list(dict.fromkeys(re.findall(r'\[([RCMB]\d+)\]', text)))
            return e.GradeResult(grades=[e.DocumentGrade(doc_id=doc_id,is_relevant=True,season_match='일치',
                covered_aspects=[aspect for values in e.BASE_ASPECTS.values() for aspect in values] + ['데님'],reason='시즌과 목적이 일치한다.') for doc_id in ids])
        if schema is e.RewriteResult:
            return e.RewriteResult(query='2027 summer fashion lightweight materials',reason='누락 측면 검색')
        if schema in e.SIGNAL_SCHEMAS.values():
            doc_id = re.search(r'\[([RCMB]\d+)\]', text)[1]
            signal = e.Signal(name='린넨 셔츠 / 블루',description='자료에서 확인한 가벼운 상품이다.',evidence_ids=[doc_id])
            if schema is e.BrandProfile:
                return schema(positioning=signal,target_customer=signal,price_range=signal,core_categories=[signal],design_identity=signal)
            return schema(**{field:[signal] for field in schema.model_fields})
        if schema is e.ProductStrategyReport:
            return e.ProductStrategyReport(season='2027 S/S',brand='ZARA',executive_summary='일상에서 활용하는 가벼운 상품 전략이다.',
                runway_insight='런웨이 신호다.',color_insight='블루 컬러다.',current_market_insight='실용적 소비다.',
                brand_insight='베이직 중심이다.',signal_connection='네 분야의 신호가 연결된다.',final_note='내부 자료를 함께 확인한다.',
                product_directions=[e.ProductDirection(direction_name=f'전략 {i}',adoption_level='선택적 반영',rationale='확인된 자료를 반영한다.',
                    recommended_items=[f'셔츠 {i}'],recommended_colors=['블루'],recommended_materials=['린넨'],silhouette_or_detail=['여유로운 핏'],
                    caution='자료의 한계를 확인한다.',evidence_ids=['R1','C1','M1','B1'],signal_sources=e.AXES) for i in range(1,4)])
        if schema is e.CriticReview:
            return e.CriticReview(issues=[])
        raise AssertionError(schema)


@pytest.fixture
def configured(monkeypatch):
    # Restore module-level injected dependencies after each test.
    for name in ['model','judge_model','search_tool','planner_model','grade_model','rewrite_model','strategy_model','critic_model']:
        monkeypatch.setattr(e,name,getattr(e,name))
    e.configure(Model(),Model(),Search())
    return e


@pytest.fixture
def result(configured):
    return configured.analyze_product_strategy('2027 S/S','ZARA','한국 20-30대 일상 상품 기획')
