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
            data=json.loads(text.split('\n')[-1]); question=data['planning_request']
            kind='styling' if '코디' in question else 'trend_analysis' if '트렌드' in question and '기획' not in question else 'product_strategy'
            brand=data.get('brand_override') if data.get('brand_override_provided') else 'ZARA' if 'ZARA' in question or '자라' in question else None
            axes=(['color','market'] if kind=='styling' else ['color'] if kind=='trend_analysis' else ['runway','color','market']+(['brand'] if brand else []))
            needs_brand=not brand and ('브랜드' in question or '우리 브랜드' in question) and not any(term in question for term in ('브랜드 제한 없이','브랜드 없는','브랜드 미지정'))
            if needs_brand: axes=list(dict.fromkeys(axes+['brand']))
            match=re.search(r'(20\d{2})\s*(S/?S|F/?W)',question)
            return PlanningDraft(runway_season=match.group(0) if match and 'runway' in axes else None,
                brand=brand,brand_scope='needs_clarification' if needs_brand else 'specific' if brand else 'unspecified',
                brand_evidence='ZARA' if brand else '',report_type=data.get('report_type_override') or kind,
                selected_axes=data.get('selected_axes_override') or axes, wearing_season='여름' if '여름' in question else None,
                time_scope='최근' if '요즘' in question else None,
                axis_reasons={axis:'질문 목적에 필요한 조사' for axis in axes},
                report_direction=data.get('direction_override') or question,reason='질문 조건에 따른 분석 방향')
        if schema is e.GeneralProductReport:
            data=json.loads(text.split('\n')[-1])
            ids=list(dict.fromkeys(re.findall(r'근거: ([RCMB]\d+)',data['signals'])))
            return e.GeneralProductReport(executive_summary='일상 데님 상품기획이다.',final_note='내부 자료도 검토한다.',limitations=[],
                product_directions=[e.ProductDirection(direction_name=f'기획 {i}',adoption_level='선택적 반영',rationale='확인한 신호와 요청을 반영한다.',
                    recommended_items=[f'셔츠 {i}'],recommended_colors=['블루'] if 'C1' in ids else [],recommended_materials=['린넨'] if 'R1' in ids else [],
                    silhouette_or_detail=['여유로운 핏'],caution='자료 한계를 확인한다.',evidence_ids=ids,signal_sources=[]) for i in range(1,4)])
        if schema is e.SelectedResearchPlan:
            data=json.loads(text.split('\n')[-1])
            return e.SelectedResearchPlan(request_profile=e.RequestProfile(target_customer='명시되지 않음',
                focus_categories=[],constraints=[],priority='균형'),
                axes={axis:e.AxisPlan(focus_keywords=['denim'] if '데님' in data['request'] else [],query='',extra_aspect='') for axis in data['selected_axes']})
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
