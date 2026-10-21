from .schemas import RequestProfile, AxisResult, ProductStrategyReport, Critique, StylingReport, TrendAnalysisReport, GeneralProductReport
import operator
from typing import Annotated, TypedDict


def merge_dict(left: dict | None, right: dict | None) -> dict:
    # 병렬 Research 결과를 하나의 dict로 병합하는 Reducer
    return {**(left or {}), **(right or {})}


class TrendState(TypedDict, total=False):
    # 사용자 입력
    season: str | None
    runway_season: str | None
    runway_season_source: str
    wearing_season: str | None
    time_scope: str | None
    brand_scope: str
    brand: str | None
    planning_request: str
    report_type: str
    selected_axes: list[str]
    domain_status: str
    domain_reason: str
    intent: str
    assumptions: list[str]
    axis_reasons: dict[str, str]
    schema_version: int
    requested_at: str
    quality_status: str
    research_status: dict[str, str]

    # ① Input Normalizer
    season_terms: list[str]
    today: str

    # ② Research Planner
    request_profile: RequestProfile
    research_plan: dict[str, dict]

    # ③ Research ×4 (병렬 쓰기 → Reducer 필요)
    research_results: Annotated[dict[str, AxisResult], merge_dict]

    # ④ Strategy ↔ ⑤ Critic
    report: ProductStrategyReport | StylingReport | TrendAnalysisReport | GeneralProductReport
    critique: Critique
    revision_count: int

    # ⑥ Report Builder
    final_markdown: str

    # 모든 노드의 실행 기록
    trace: Annotated[list[dict], operator.add]


class ResearchState(TypedDict, total=False):
    axis: str
    season: str | None
    runway_season: str | None
    wearing_season: str | None
    time_scope: str | None
    brand_scope: str
    brand: str | None
    season_terms: list[str]
    today: str
    plan: dict

    pending_queries: list[str]   # 다음 Search에서 실행할 검색어
    queries_tried: list[str]     # 지금까지 시도한 검색어 (반복 방지)
    candidates: list[dict]       # 검색으로 모은 문서 전체 (URL 중복 제거)
    new_doc_ids: list[str]       # 이번 Search에서 새로 들어온 문서
    grades: dict[str, dict]      # 문서별 평가 결과
    missing_aspects: list[str]   # 아직 확인하지 못한 측면
    attempt: int                 # 검색 시도 횟수
    search_errors: int           # 상세 오류는 저장하지 않고 실패 횟수만 보존

    result: AxisResult
    trace: Annotated[list[dict], operator.add]


