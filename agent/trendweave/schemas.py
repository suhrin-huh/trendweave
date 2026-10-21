from typing import Literal
from pydantic import BaseModel, Field

Axis = Literal['runway', 'color', 'market', 'brand']
ReportType = Literal['product_strategy', 'styling', 'trend_analysis']


class SelectedResearchPlan(BaseModel):
    request_profile: 'RequestProfile'
    axes: dict[Axis, 'AxisPlan']


class Outfit(BaseModel):
    name: str
    composition: list[str]
    color_combination: list[str]
    occasion: str
    rationale: str
    caution: str
    evidence_ids: list[str]


class StylingReport(BaseModel):
    executive_summary: str
    outfits: list[Outfit] = Field(description='근거 있는 코디 최대 3안. 충분한 근거가 있으면 3안, 부족하면 개수를 줄이고 limitations에 이유 표시')
    usage_tips: list[str]
    limitations: list[str]


class GeneralProductReport(BaseModel):
    executive_summary: str
    product_directions: list['ProductDirection']
    final_note: str
    limitations: list[str]


class TrendFinding(BaseModel):
    name: str
    observation: str
    interpretation: str
    application: str
    evidence_ids: list[str]


class TrendAnalysisReport(BaseModel):
    executive_summary: str
    findings: list[TrendFinding]
    watch_points: list[str]
    limitations: list[str]


# ② Research Planner
class RequestProfile(BaseModel):
    target_customer: str = Field(description="요청에서 읽은 타깃 고객. 명시되지 않았으면 '명시되지 않음'")
    focus_categories: list[str] = Field(description="요청에서 중점으로 언급한 상품군. 없으면 빈 리스트")
    constraints: list[str] = Field(description="가격, 용도, 기능 등 요청에 포함된 조건. 없으면 빈 리스트")
    priority: Literal["트렌드 반영 우선", "실용성 우선", "균형"] = Field(
        description="요청이 트렌드 반영과 실용성 중 무엇을 더 중시하는가"
    )


class AxisPlan(BaseModel):
    focus_keywords: list[str] = Field(description="이 축의 기본 검색어에 덧붙일 영어 키워드 0~3개")
    query: str = Field(description="이 축에서 요청에 맞춰 추가로 실행할 영어 검색어 1개")
    extra_aspect: str = Field(
        description="요청 때문에 이 축에서 추가로 꼭 확인해야 할 정보 측면 1개(한국어 짧은 명사구). 없으면 빈 문자열"
    )


class ResearchPlan(BaseModel):
    request_profile: RequestProfile
    runway: AxisPlan
    color: AxisPlan
    market: AxisPlan
    brand: AxisPlan


# ③ Research - Grade
class DocumentGrade(BaseModel):
    doc_id: str = Field(description="평가한 문서 번호 (예: R1)")
    is_relevant: bool = Field(description="이 축의 목적과 평가 기준에 맞는 자료인가")
    season_match: Literal["일치", "불일치", "확인 불가", "해당 없음"] = Field(
        description="Runway의 컬렉션 시즌과의 일치 여부. Color·Market·Brand는 '해당 없음'"
    )
    covered_aspects: list[str] = Field(description="이 문서가 실제로 다루는 필수 확인 측면 (주어진 목록의 문자열 그대로)")
    reason: str = Field(description="판단 이유 한 줄")


class GradeResult(BaseModel):
    grades: list[DocumentGrade]


# ③ Research - Rewrite
class RewriteResult(BaseModel):
    query: str = Field(description="새 영어 검색어 1개")
    reason: str = Field(description="이전 검색어에서 무엇을 왜 바꿨는지 한 줄 (한국어)")


# ③ Research - Extract
class Signal(BaseModel):
    name: str = Field(description="신호 이름 (예: 오버사이즈 테일러링)")
    description: str = Field(description="자료에서 확인한 내용 1~2문장")
    evidence_ids: list[str] = Field(description="근거 문서 번호 (예: ['R1', 'R3'])")


class RunwaySignal(BaseModel):
    silhouettes: list[Signal] = Field(description="실루엣")
    materials: list[Signal] = Field(description="소재")
    details: list[Signal] = Field(description="디테일")
    key_items: list[Signal] = Field(description="핵심 아이템")


class ColorSignal(BaseModel):
    key_colors: list[Signal] = Field(description="핵심 컬러 (컬러명 + 무드 설명)")
    color_moods: list[Signal] = Field(description="전반적인 컬러 무드 및 팔레트 방향")


class MarketSignal(BaseModel):
    trending_styles: list[Signal] = Field(description="현재 주목받는 스타일 및 아이템")
    consumer_behaviors: list[Signal] = Field(description="소비자 반응과 구매 행동")


class BrandProfile(BaseModel):
    positioning: Signal = Field(description="브랜드 포지셔닝")
    target_customer: Signal = Field(description="타깃 고객")
    price_range: Signal = Field(description="가격대")
    core_categories: list[Signal] = Field(description="핵심 상품군")
    design_identity: Signal = Field(description="디자인 정체성")


SIGNAL_SCHEMAS = {
    "runway": RunwaySignal,
    "color": ColorSignal,
    "market": MarketSignal,
    "brand": BrandProfile,
}

SIGNAL_FIELD_LABELS = {
    "silhouettes": "실루엣",
    "materials": "소재",
    "details": "디테일",
    "key_items": "핵심 아이템",
    "key_colors": "핵심 컬러",
    "color_moods": "컬러 무드",
    "trending_styles": "주목받는 스타일",
    "consumer_behaviors": "소비자 반응",
    "positioning": "포지셔닝",
    "target_customer": "타깃 고객",
    "price_range": "가격대",
    "core_categories": "핵심 상품군",
    "design_identity": "디자인 정체성",
}


# Research 결과를 상위 그래프로 넘기는 공통 포장 (LLM 출력 아님)
class AxisResult(BaseModel):
    axis: str
    signal: RunwaySignal | ColorSignal | MarketSignal | BrandProfile
    documents: list[dict]
    confidence: Literal["높음", "보통", "낮음"]
    data_gaps: list[str]
    attempts: int
    queries: list[str]
    status: Literal['succeeded', 'insufficient', 'failed'] = 'succeeded'


# ④ Strategy Writer
class ProductDirection(BaseModel):
    direction_name: str = Field(description="상품기획 방향 이름")
    adoption_level: Literal["적극 반영", "선택적 반영", "신중 검토"] = Field(
        description="자료와 요청 조건을 고려한 적용 수준. 브랜드가 지정된 경우에만 브랜드 특성을 고려"
    )
    rationale: str = Field(
        description="Signal(근거 번호)과 요청의 타깃·조건을 연결한 판단 이유 2~3문장. 특정 브랜드가 지정되고 관련 근거가 있는 경우에만 브랜드 적합성도 설명"
    )
    recommended_items: list[str] = Field(description="추천 상품 아이템")
    recommended_colors: list[str] = Field(description="추천 컬러")
    recommended_materials: list[str] = Field(description="추천 소재")
    silhouette_or_detail: list[str] = Field(description="추천 실루엣 또는 디테일")
    caution: str = Field(description="적용 시 주의점")
    evidence_ids: list[str] = Field(description="이 방향의 근거가 된 문서 번호 (Signal에 표시된 번호만 사용)")
    signal_sources: list[Literal["runway", "color", "market", "brand"]] = Field(
        description="근거로 사용한 Research 축 (작성 후 코드가 evidence_ids 접두어로 다시 계산한다)"
    )


class ProductStrategyReport(BaseModel):
    season: str = Field(description="분석 대상 시즌")
    brand: str = Field(description="분석 대상 브랜드")
    executive_summary: str = Field(description="전체 상품기획 방향 요약")
    runway_insight: str = Field(description="Runway Signal에서 확인한 핵심 신호")
    color_insight: str = Field(description="Color Signal에서 확인한 핵심 신호")
    current_market_insight: str = Field(description="Market Signal에서 확인한 핵심 신호")
    brand_insight: str = Field(description="Brand Profile에서 확인한 핵심 특성")
    signal_connection: str = Field(description="네 축의 신호가 어떻게 연결되는지 설명")
    product_directions: list[ProductDirection] = Field(description="우선순위가 높은 상품기획 방향 3개")
    final_note: str = Field(description="기업 의사결정 시 함께 고려할 최종 주의사항")


# ⑤ Strategy Critic
class Issue(BaseModel):
    target: str = Field(description="문제 위치 (예: direction_2.recommended_colors)")
    type: Literal["grounding", "brand_fit", "request_fit", "overclaim", "confidence", "format"] = Field(
        description="문제 유형"
    )
    severity: Literal["major", "minor"] = Field(
        description="major: 리포트를 신뢰할 수 없게 만드는 문제 / minor: 표현 수준의 개선 사항"
    )
    detail: str = Field(description="무엇이 문제인가")
    fix: str = Field(description="어떻게 고쳐야 하는가")


class CriticReview(BaseModel):
    issues: list[Issue] = Field(description="발견한 문제 목록. 문제가 없으면 빈 리스트")


class Critique(BaseModel):
    passed: bool
    issues: list[Issue]
    llm_reviewed: bool = True   # LLM 검토가 실패해 규칙 검사만 적용했으면 False
