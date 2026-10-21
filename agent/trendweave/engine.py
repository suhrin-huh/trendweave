"""Port of reference/AI_Agent_설계_trendweave.ipynb; no notebook/UI side effects.

Models are injected once at startup. All invocation state lives in LangGraph.
"""
from .schemas import *
from .state import TrendState, ResearchState
from .config import *
from langgraph.config import get_stream_writer

model = judge_model = search_tool = None
planner_model = grade_model = rewrite_model = strategy_model = critic_model = None


def configure(primary, judge, search):
    global model, judge_model, search_tool
    global planner_model, grade_model, rewrite_model, strategy_model, critic_model
    model, judge_model, search_tool = primary, judge, search
    planner_model = primary.with_structured_output(ResearchPlan)
    grade_model = judge.with_structured_output(GradeResult)
    rewrite_model = primary.with_structured_output(RewriteResult)
    strategy_model = primary.with_structured_output(ProductStrategyReport)
    critic_model = judge.with_structured_output(CriticReview)


def trace_event(step: str, message: str, axis: str = "") -> dict:
    event = {"step": step, "axis": axis, "message": message}
    try:
        get_stream_writer()(event)
    except RuntimeError:
        pass
    return event

import re
import time
from urllib.parse import urlparse


def build_season_variants(season: str) -> list[str]:
    # 시즌 표기 변형 생성 (기존 설계와 동일)
    text = season.strip()
    upper = text.upper().replace(" ", "")
    year_match = re.search(r"20\d{2}", upper)

    if not year_match:
        return [text]

    year = year_match.group(0)
    short_year = year[-2:]

    if any(token in upper for token in ["S/S", "SS", "SPRING", "SUMMER"]):
        return [text, f"Spring Summer {year}", f"Spring/Summer {year}", f"SS{short_year}"]

    if any(token in upper for token in ["F/W", "FW", "FALL", "WINTER", "AUTUMN"]):
        return [text, f"Fall Winter {year}", f"Autumn Winter {year}", f"FW{short_year}"]

    return [text]


def build_research_queries(season: str, brand: str, today: str) -> dict[str, str]:
    # 목적별 기본 검색어 템플릿 (기존 설계 기반, 축 이름은 market으로 통일하고 날짜 기준은 state의 today로 맞춤)
    season_terms = " OR ".join(f'"{item}"' for item in build_season_variants(season))
    current_year = today[:4]

    return {
        "runway": (
            f"({season_terms}) fashion week runway collection trends "
            "silhouette material styling key items"
        ),
        "color": (
            f"({season_terms}) fashion color forecast Pantone color trend report "
            "runway palette"
        ),
        "market": (
            f"{current_year} current fashion trends retail street style consumer "
            "silhouette material color key items"
        ),
        "brand": (
            f'"{brand}" brand positioning target customer core products '
            "design style price strategy Korea fashion"
        ),
    }


def safe_search(query: str, options: dict, retries: int = 1) -> tuple[list[dict], str]:
    # Tavily 검색. 실패하면 재시도하고, 끝내 실패하면 빈 결과와 오류 메시지를 반환
    last_error = ""
    for attempt in range(retries + 1):
        try:
            raw = search_tool.invoke({"query": query, **options})

            # langchain-tavily는 오류를 예외 대신 {"error": ...}로 돌려주므로 직접 확인한다
            if isinstance(raw, dict) and "error" in raw:
                message = str(raw["error"])
                if message.startswith("No search results"):
                    return [], ""          # 정상적인 0건
                raise RuntimeError(message)  # API 키, 할당량, 네트워크 오류

            results = raw.get("results", []) if isinstance(raw, dict) else raw
            return [item for item in results if isinstance(item, dict)], ""
        except Exception as error:
            last_error = str(error)
            time.sleep(1 + attempt)
    return [], last_error


def documents_to_context(documents: list[dict], max_chars: int = 1500) -> str:
    # 문서를 Prompt용 텍스트로 변환
    blocks = []
    for item in documents:
        blocks.append(
            f"[{item['doc_id']}]\n"
            f"제목: {item['title']}\n"
            f"URL: {item['url']}\n"
            f"발행일: {item['published_date'] or '확인되지 않음'}\n"
            f"내용: {item['content'][:max_chars]}"
        )
    return "\n\n".join(blocks)


def is_excluded_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in EXCLUDED_DOMAINS)


def remove_brand(query: str, brand: str) -> str:
    # 브랜드와 무관한 시장 흐름을 조사하는 축(runway, color, market)의 검색어에서 브랜드명을 제거
    return re.sub(re.escape(brand), "", query, flags=re.IGNORECASE).replace("  ", " ").strip()


def require(result, name: str):
    # Structured Output이 비어 있으면 예외를 내서 RetryPolicy가 다시 시도하게 한다
    if result is None:
        raise ValueError(f"{name}: LLM이 구조화된 결과를 반환하지 않았습니다.")
    return result

def input_normalizer(state: TrendState) -> dict:
    season = state.get("season", "").strip()
    brand = state.get("brand", "").strip()
    planning_request = state.get("planning_request", "").strip()

    if not season:
        raise ValueError("시즌을 입력하세요.")
    if not brand:
        raise ValueError("브랜드를 선택하세요.")
    if not planning_request:
        raise ValueError("상품기획 요청을 입력하세요.")

    season_terms = build_season_variants(season)

    return {
        "season": season,
        "brand": brand,
        "planning_request": planning_request,
        "season_terms": season_terms,
        "today": date.today().isoformat(),
        "revision_count": 0,
        "trace": [trace_event("normalizer", f"시즌 표기: {', '.join(season_terms)}")],
    }

from langchain_core.prompts import ChatPromptTemplate

planner_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
당신은 SPA 패션 브랜드 상품기획팀의 리서치 플래너입니다.
사용자의 상품기획 요청을 해석하고, 아래 4개 Research 축 각각에서 무엇을 중점적으로 검색할지 계획합니다.

[Research 축]
- runway: {runway_purpose}
- color: {color_purpose}
- market: {market_purpose}
- brand: {brand_purpose}

[작성 규칙]
- request_profile에는 요청에 실제로 적힌 내용만 정리하세요. 적혀 있지 않은 조건을 지어내지 마세요.
- focus_keywords와 query는 웹 검색용 영어로 작성하세요.
- 요청에 특정 상품군, 소재, 용도가 있으면 해당 축의 키워드와 검색어에 반영하세요.
- runway 및 color 검색어에는 분석 시즌을 넣으세요.
- brand 검색어에는 분석 브랜드명을 넣고, 경쟁 브랜드는 넣지 마세요.
- extra_aspect는 요청이 특정 상품군, 소재, 기능(예: 데님, 린넨, 냉감 기능)을 지정했을 때만,
  그 대상을 짧은 명사로 작성하세요. 문서에서 사실로 확인할 수 있는 정보여야 합니다.
- 타깃 고객, 지역, 활용도, 전략처럼 검색 자료에서 사실로 확인하기 어려운 내용은 extra_aspect로 만들지 마세요.
  이런 요청 조건은 request_profile에만 정리하면 Strategy 단계에서 반영됩니다. 해당하지 않으면 빈 문자열로 두세요.
""",
    ),
    (
        "human",
        """
분석 시즌: {season} ({season_terms})
분석 브랜드: {brand}
오늘 날짜: {today}
상품기획 요청: {planning_request}
""",
    ),
])



def fallback_research_plan(state: TrendState) -> ResearchPlan:
    # Planner 실패 시 템플릿 검색어만으로 진행하기 위한 기본 계획
    empty_axis = AxisPlan(focus_keywords=[], query="", extra_aspect="")
    return ResearchPlan(
        request_profile=RequestProfile(
            target_customer="명시되지 않음",
            focus_categories=[],
            constraints=[state["planning_request"]],
            priority="균형",
        ),
        runway=empty_axis,
        color=empty_axis,
        market=empty_axis,
        brand=empty_axis,
    )


def research_planner(state: TrendState) -> dict:
    try:
        plan = require(
            planner_model.invoke(
                planner_prompt.format_messages(
                    runway_purpose=AXIS_PURPOSE["runway"],
                    color_purpose=AXIS_PURPOSE["color"],
                    market_purpose=AXIS_PURPOSE["market"],
                    brand_purpose=AXIS_PURPOSE["brand"],
                    season=state["season"],
                    season_terms=", ".join(state["season_terms"]),
                    brand=state["brand"],
                    today=state["today"],
                    planning_request=state["planning_request"],
                )
            ),
            "Research Planner",
        )
        planner_note = "요청 해석 완료"
    except Exception as error:
        plan = fallback_research_plan(state)
        planner_note = f"Planner 실패로 템플릿 검색어 사용 (외부 도구 호출 실패)"

    templates = build_research_queries(state["season"], state["brand"], state["today"])
    research_plan = {}

    for axis in AXES:
        axis_plan: AxisPlan = getattr(plan, axis)
        focus = [keyword.strip() for keyword in axis_plan.focus_keywords if keyword.strip()][:3]

        # 첫 검색어: 템플릿 + 초점 키워드 / 두 번째 검색어: Planner가 만든 검색어
        queries = [f"{templates[axis]} {' '.join(focus)}".strip()]
        if axis_plan.query.strip():
            queries.append(axis_plan.query.strip())
        if axis != "brand":
            queries = [remove_brand(query, state["brand"]) for query in queries]

        # 요청에 중점 상품군이 있을 때만, runway 및 market 축에만 측면 추가를 허용
        # (브랜드 포지셔닝 문서나 컬러 전망에서는 '데님' 같은 측면을 확인하기 어려워 재검색만 반복되기 때문)
        aspects = list(BASE_ASPECTS[axis])
        extra = axis_plan.extra_aspect.strip()
        if extra and extra not in aspects and plan.request_profile.focus_categories and axis in ("runway", "market"):
            aspects.append(extra)

        research_plan[axis] = {
            "focus_keywords": focus,
            "queries": queries,
            "required_aspects": aspects,
        }

    profile = plan.request_profile
    summary = (
        f"{planner_note} - 타깃: {profile.target_customer} / "
        f"중점 상품군: {', '.join(profile.focus_categories) or '없음'} / 우선순위: {profile.priority}"
    )

    return {
        "request_profile": profile,
        "research_plan": research_plan,
        "trace": [trace_event("planner", summary)],
    }

def research_search(state: ResearchState) -> dict:
    axis = state["axis"]
    candidates = list(state.get("candidates", []))
    seen_urls = {doc["url"] for doc in candidates}
    queries = state.get("pending_queries", [])
    options = search_options(axis, state["today"])

    new_docs = []
    errors = []

    for query in queries:
        results, error = safe_search(query, options)
        if error:
            errors.append(error)

        for item in results:
            url = item.get("url", "")
            if not url or url in seen_urls or is_excluded_url(url):
                continue
            seen_urls.add(url)
            new_docs.append({
                "doc_id": f"{AXIS_PREFIX[axis]}{len(candidates) + len(new_docs) + 1}",
                "title": item.get("title", "제목 없음"),
                "url": url,
                "content": (item.get("content", "") or "")[:1800],
                "published_date": item.get("published_date", ""),
            })

    attempt = state.get("attempt", 0) + 1
    shown = " | ".join(query if len(query) <= 120 else query[:117] + "..." for query in queries)
    message = f"{attempt}차 검색: 새 문서 {len(new_docs)}건 / 검색어: {shown}"
    if errors:
        message += f" (검색 오류 {len(errors)}건)"

    return {
        "candidates": candidates + new_docs,
        "new_doc_ids": [doc["doc_id"] for doc in new_docs],
        "queries_tried": state.get("queries_tried", []) + queries,
        "pending_queries": [],
        "attempt": attempt,
        "trace": [trace_event("search", message, axis)],
    }

grade_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
당신은 패션 리서치 자료의 품질을 평가하는 검수자입니다.
아래 문서들이 '{axis_label}'의 목적에 맞는지 문서마다 평가하세요.

[이 축의 목적]
{purpose}

[평가 기준]
{criteria}
{common_criteria}

[필수 확인 측면]
{aspects}

[규칙]
- 모든 문서에 대해 doc_id를 그대로 사용해 하나씩 평가하세요.
- covered_aspects에는 필수 확인 측면 목록의 문자열을 그대로 쓰고, 문서에서 실제로 구체적인 내용이 확인되는 측면만 넣으세요.
- 제목만으로 판단하지 말고 내용을 근거로 판단하세요.
- reason은 한국어 한 줄로 작성하세요.
""",
    ),
    (
        "human",
        """
분석 시즌: {season} ({season_terms})
분석 브랜드: {brand}
오늘 날짜: {today}

[평가할 문서]
{documents}
""",
    ),
])



def is_valid_grade(grade: dict) -> bool:
    return grade["is_relevant"] and grade["season_match"] != "불일치"


def normalize_aspects(covered: list[str], required: list[str]) -> list[str]:
    # LLM이 쓴 측면 이름을 필수 측면 목록의 표기로 맞춘다.
    # 같은 이름이거나 LLM이 더 길게 쓴 경우('핵심 아이템' → '아이템')만 인정한다.
    # 반대 방향('소재' → '데님 소재')을 인정하면 요청으로 추가한 측면이 항상 확인된 것으로 처리된다.
    covered = [item.strip() for item in covered if item.strip()]
    return [aspect for aspect in required if any(aspect == item or aspect in item for item in covered)]


def research_grade(state: ResearchState) -> dict:
    axis = state["axis"]
    required = state["plan"]["required_aspects"]
    grades = dict(state.get("grades", {}))
    new_ids = set(state.get("new_doc_ids", []))
    new_docs = [doc for doc in state["candidates"] if doc["doc_id"] in new_ids]

    if new_docs:
        result = require(
            grade_model.invoke(
                grade_prompt.format_messages(
                    axis_label=AXIS_LABELS[axis],
                    purpose=AXIS_PURPOSE[axis],
                    criteria=GRADE_CRITERIA[axis],
                    common_criteria=COMMON_GRADE_CRITERIA,
                    aspects=", ".join(required),
                    season=state["season"],
                    season_terms=", ".join(state["season_terms"]),
                    brand=state["brand"],
                    today=state["today"],
                    documents=documents_to_context(new_docs, max_chars=1200),
                )
            ),
            "Grade",
        )
        returned = {grade.doc_id: grade for grade in result.grades}

        for doc in new_docs:
            grade = returned.get(doc["doc_id"])
            if grade is None:
                # 평가가 누락된 문서는 유효로 보지 않는다
                grades[doc["doc_id"]] = {
                    "is_relevant": False,
                    "season_match": "확인 불가",
                    "covered_aspects": [],
                    "reason": "평가 결과 누락",
                }
            else:
                grades[doc["doc_id"]] = {
                    "is_relevant": grade.is_relevant,
                    # Market 및 Brand 축은 시즌을 판단하지 않으므로 코드가 '해당 없음'으로 덮어쓴다
                    "season_match": "해당 없음" if axis in ("market", "brand") else grade.season_match,
                    "covered_aspects": normalize_aspects(grade.covered_aspects, required),
                    "reason": grade.reason,
                }

    # 유효 문서와 누락 측면은 코드가 계산
    valid_ids = [doc_id for doc_id, grade in grades.items() if is_valid_grade(grade)]
    covered = {aspect for doc_id in valid_ids for aspect in grades[doc_id]["covered_aspects"]}
    missing = [aspect for aspect in required if aspect not in covered]

    new_valid = sum(1 for doc_id in new_ids if doc_id in valid_ids)
    message = (
        f"평가: 새 문서 {len(new_docs)}건 중 유효 {new_valid}건 "
        f"(누적 유효 {len(valid_ids)}건) / 누락 측면: {', '.join(missing) or '없음'}"
    )

    return {
        "grades": grades,
        "missing_aspects": missing,
        "trace": [trace_event("grade", message, axis)],
    }

def valid_doc_ids(state: ResearchState) -> list[str]:
    return [doc_id for doc_id, grade in state.get("grades", {}).items() if is_valid_grade(grade)]


def route_after_grade(state: ResearchState) -> Literal["rewrite", "extract"]:
    enough_docs = len(valid_doc_ids(state)) >= MIN_VALID_DOCS
    no_missing = not state.get("missing_aspects")

    if enough_docs and no_missing:
        return "extract"
    if state.get("attempt", 0) < MAX_SEARCH_ATTEMPTS:
        return "rewrite"
    return "extract"

rewrite_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
당신은 패션 리서치용 웹 검색어를 개선하는 담당자입니다.
'{axis_label}'의 이전 검색이 충분한 자료를 찾지 못했습니다. 실패 원인을 보고 새 검색어 1개를 만드세요.

[이 축의 목적]
{purpose}

[재작성 원칙]
- 다른 시즌 자료가 많았다면 시즌 표기를 하나로 좁혀 명시하세요.
- 누락된 측면이 있다면 그 측면을 직접 키워드로 넣으세요.
- 결과가 너무 일반적이었다면 범위를 좁히고, 결과가 거의 없었다면 범위를 넓히세요.
- 이미 시도한 검색어와 같거나 거의 같은 검색어는 만들지 마세요.
- 검색어는 영어로, 15단어 이내로 작성하세요.
- brand 축이라면 분석 브랜드명을 반드시 넣고 경쟁 브랜드는 넣지 마세요.
- runway, color, market 축이라면 브랜드명을 넣지 마세요. 이 축들은 브랜드와 무관한 시장 전체 흐름을 조사합니다.
""",
    ),
    (
        "human",
        """
분석 시즌: {season} ({season_terms})
분석 브랜드: {brand}
오늘 날짜: {today}

[시도한 검색어]
{queries_tried}

[현재 유효 문서 수]
{valid_count}건 (목표 {min_valid}건 이상)

[누락된 측면]
{missing}

[탈락한 문서와 이유]
{rejected}
""",
    ),
])


# 재작성 검색어가 이전과 같을 때 쓰는 축별 기본 키워드
AXIS_FALLBACK_KEYWORDS = {
    "runway": "runway collection trend review",
    "color": "color trend forecast palette",
    "market": "consumer fashion trend report",
    "brand": "brand strategy products",
}



def research_rewrite(state: ResearchState) -> dict:
    axis = state["axis"]
    grades = state.get("grades", {})
    titles = {doc["doc_id"]: doc["title"] for doc in state["candidates"]}

    rejected = [
        f"- {titles.get(doc_id, doc_id)}: {grade['reason']}"
        for doc_id, grade in grades.items()
        if not is_valid_grade(grade)
    ]

    result = require(
        rewrite_model.invoke(
            rewrite_prompt.format_messages(
                axis_label=AXIS_LABELS[axis],
                purpose=AXIS_PURPOSE[axis],
                season=state["season"],
                season_terms=", ".join(state["season_terms"]),
                # 브랜드명은 brand 축에만 알려준다
                brand=state["brand"] if axis == "brand" else "(이 축에서는 사용하지 않음)",
                today=state["today"],
                queries_tried="\n".join(f"- {query}" for query in state.get("queries_tried", [])),
                valid_count=len(valid_doc_ids(state)),
                min_valid=MIN_VALID_DOCS,
                missing=", ".join(state.get("missing_aspects", [])) or "없음",
                rejected="\n".join(rejected[:8]) or "없음",
            )
        ),
        "Rewrite",
    )

    query = result.query.strip()
    if axis != "brand":
        query = remove_brand(query, state["brand"])

    if query.lower() in {tried.lower() for tried in state.get("queries_tried", [])}:
        # 같은 검색어가 나오면 시즌 표기와 축별 기본 키워드로 다른 검색어를 만든다
        season_text = state["season_terms"][-1] if axis != "market" else state["today"][:4]
        prefix = state["brand"] if axis == "brand" else season_text
        query = f"{prefix} {AXIS_FALLBACK_KEYWORDS[axis]}"

    return {
        "pending_queries": [query],
        "trace": [trace_event("rewrite", f"재검색: \"{query}\" - {result.reason}", axis)],
    }

extract_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
당신은 '{axis_label}' 분야의 패션 리서치 분석가입니다.
제공된 문서에서 이 축의 목적에 맞는 신호를 추출해 구조화하세요.

[이 축의 목적]
{purpose}
{axis_guide}
[규칙]
- 문서에 실제로 적힌 내용만 추출하세요. 문서에 없는 트렌드를 일반 상식으로 채우지 마세요.
- 컬러명, 소재명, 아이템명은 한국어 뒤 괄호에 원문 표기를 그대로 남기세요. 예: 테라코타(Terracotta)
- 모든 Signal의 evidence_ids에는 근거가 된 문서 번호(예: {prefix}1)를 넣으세요.
- 여러 문서에서 반복되는 신호는 하나로 합치고 근거 번호를 모두 적으세요.
- 리스트 항목은 항목당 2~5개 정도로, 중요한 것부터 작성하세요.
- 단일 항목인데 자료에서 확인되지 않으면 description에 '확인되지 않음'이라고 쓰고 evidence_ids는 빈 리스트로 두세요.
- 결과는 한국어로 작성하고, description은 '~다'체 평서문으로 쓰세요.
""",
    ),
    (
        "human",
        """
분석 시즌: {season}
분석 브랜드: {brand}

[문서]
{documents}
""",
    ),
])


# 축별 추출 안내
EXTRACT_GUIDE = {
    "runway": "",
    "color": "- 공식 팔레트(Pantone 등)에 나온 컬러명을 우선 추출하고, 컬러 코드가 있으면 함께 적으세요.\n",
    "market": "- trending_styles에는 구체적인 아이템 및 스타일명만 넣고, 가치관 및 소비 성향은 consumer_behaviors에 넣으세요.\n",
    "brand": "",
}


def empty_signal(axis: str):
    # 유효 문서가 하나도 없을 때 쓰는 빈 Signal
    unknown = Signal(name="확인되지 않음", description="유효한 자료를 찾지 못했다.", evidence_ids=[])
    if axis == "brand":
        return BrandProfile(
            positioning=unknown,
            target_customer=unknown,
            price_range=unknown,
            core_categories=[],
            design_identity=unknown,
        )
    schema = SIGNAL_SCHEMAS[axis]
    return schema(**{name: [] for name in schema.model_fields})


def clean_evidence(signal_obj, allowed_ids: set[str]):
    # 존재하지 않는 근거 번호를 지우고, 근거가 없는 리스트 Signal은 제거 (Grounding 강제)
    for name in type(signal_obj).model_fields:
        value = getattr(signal_obj, name)
        if isinstance(value, list):
            kept = []
            for item in value:
                item.evidence_ids = [eid for eid in item.evidence_ids if eid in allowed_ids]
                if item.evidence_ids:
                    kept.append(item)
            setattr(signal_obj, name, kept)
        elif isinstance(value, Signal):
            value.evidence_ids = [eid for eid in value.evidence_ids if eid in allowed_ids]
    return signal_obj


def compute_confidence(valid_count: int, missing: list[str], required: list[str]) -> str:
    if valid_count >= MIN_VALID_DOCS and not missing:
        return "높음"
    if valid_count < 2 or len(missing) * 2 >= len(required):
        return "낮음"
    return "보통"


def research_extract(state: ResearchState) -> dict:
    axis = state["axis"]
    grades = state.get("grades", {})
    required = state["plan"]["required_aspects"]
    missing = state.get("missing_aspects", [])

    # 시즌이 명확히 일치하는 문서를 우선 사용
    priority = {"일치": 0, "해당 없음": 0, "확인 불가": 1}
    valid_docs = [doc for doc in state["candidates"] if doc["doc_id"] in grades and is_valid_grade(grades[doc["doc_id"]])]
    valid_docs.sort(key=lambda doc: priority.get(grades[doc["doc_id"]]["season_match"], 2))
    used_docs = valid_docs[:MAX_DOCS_FOR_EXTRACT]

    if used_docs:
        extract_model = model.with_structured_output(SIGNAL_SCHEMAS[axis])
        signal = require(
            extract_model.invoke(
                extract_prompt.format_messages(
                    axis_label=AXIS_LABELS[axis],
                    purpose=AXIS_PURPOSE[axis],
                    axis_guide=EXTRACT_GUIDE[axis],
                    prefix=AXIS_PREFIX[axis],
                    season=state["season"],
                    brand=state["brand"],
                    documents=documents_to_context(used_docs),
                )
            ),
            "Extract",
        )
        signal = clean_evidence(signal, {doc["doc_id"] for doc in used_docs})
    else:
        signal = empty_signal(axis)

    confidence = compute_confidence(len(valid_docs), missing, required)
    result = AxisResult(
        axis=axis,
        signal=signal,
        documents=[{key: doc[key] for key in ["doc_id", "title", "url", "published_date"]} for doc in used_docs],
        confidence=confidence,
        data_gaps=missing,
        attempts=state.get("attempt", 0),
        queries=state.get("queries_tried", []),
    )

    message = f"Signal 추출: 문서 {len(used_docs)}건 사용 / 신뢰도 {confidence}"
    return {"result": result, "trace": [trace_event("extract", message, axis)]}

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy, Send

LLM_RETRY = RetryPolicy(max_attempts=2, retry_on=Exception)

research_builder = StateGraph(ResearchState)

research_builder.add_node("search", research_search)
research_builder.add_node("grade", research_grade, retry_policy=LLM_RETRY)
research_builder.add_node("rewrite", research_rewrite, retry_policy=LLM_RETRY)
research_builder.add_node("extract", research_extract, retry_policy=LLM_RETRY)

research_builder.add_edge(START, "search")
research_builder.add_edge("search", "grade")
research_builder.add_conditional_edges("grade", route_after_grade, ["rewrite", "extract"])
research_builder.add_edge("rewrite", "search")
research_builder.add_edge("extract", END)

research_subgraph = research_builder.compile()



def dispatch_research(state: TrendState) -> list[Send]:
    # 4개 축을 각각 다른 입력으로 research 노드에 보낸다
    return [
        Send(
            "research",
            {
                "axis": axis,
                "season": state["season"],
                "brand": state["brand"],
                "season_terms": state["season_terms"],
                "today": state["today"],
                "plan": state["research_plan"][axis],
                "pending_queries": state["research_plan"][axis]["queries"],
                "queries_tried": [],
                "candidates": [],
                "grades": {},
                "attempt": 0,
            },
        )
        for axis in AXES
    ]


def run_research(state: dict) -> dict:
    # Research Subgraph 실행 후 최종 결과와 실행 기록만 상위 그래프로 반환
    axis = state["axis"]
    try:
        output = research_subgraph.invoke(state, config={"recursion_limit": RECURSION_LIMIT})
        return {
            "research_results": {axis: output["result"]},
            "trace": output.get("trace", []),
        }
    except Exception as error:
        # 한 축이 실패해도 나머지 축의 결과는 살린다 (신뢰도 '낮음'의 빈 결과로 진행)
        result = AxisResult(
            axis=axis,
            signal=empty_signal(axis),
            documents=[],
            confidence="낮음",
            data_gaps=state["plan"]["required_aspects"],
            attempts=0,
            queries=[],
        )
        return {
            "research_results": {axis: result},
            "trace": [trace_event("error", f"Research 실패, 빈 결과로 진행 (외부 도구 호출 실패)", axis)],
        }

import json


def signal_to_text(result: AxisResult) -> str:
    # 축별 Signal을 Strategy 및 Critic 입력용 텍스트로 변환
    lines = [
        f"[{AXIS_LABELS[result.axis]}] 신뢰도: {result.confidence} / "
        f"확인하지 못한 측면: {', '.join(result.data_gaps) or '없음'} / 검색 시도: {result.attempts}회"
    ]
    for name in type(result.signal).model_fields:
        value = getattr(result.signal, name)
        items = value if isinstance(value, list) else [value]
        lines.append(f"- {SIGNAL_FIELD_LABELS.get(name, name)}")
        if not items:
            lines.append("  - 확인된 신호 없음")
        for item in items:
            evidence = ", ".join(item.evidence_ids) or "근거 없음"
            lines.append(f"  - {item.name}: {item.description} (근거: {evidence})")
    return "\n".join(lines)


def all_signals_text(results: dict[str, AxisResult]) -> str:
    return "\n\n".join(signal_to_text(results[axis]) for axis in AXES if axis in results)


def profile_to_text(profile: RequestProfile) -> str:
    return (
        f"- 타깃 고객: {profile.target_customer}\n"
        f"- 중점 상품군: {', '.join(profile.focus_categories) or '없음'}\n"
        f"- 조건: {', '.join(profile.constraints) or '없음'}\n"
        f"- 우선순위: {profile.priority}"
    )


strategy_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
당신은 SPA 패션 브랜드의 다음 시즌 상품기획을 지원하는 전략 담당자입니다.

당신에게는 네 Research 축에서 검증 및 구조화된 Signal이 제공됩니다.
각 Signal 끝의 (근거: R1, C2 ...)는 근거 문서 번호입니다.

[목표]
- 각 축의 핵심 신호를 간결하게 정리하세요.
- 네 축의 공통점과 차이를 연결하세요.
- 적용 수준은 다음 기준으로 정하세요.
  - 적극 반영: runway 및 color 신호가 market 신호나 브랜드 핵심 상품군에서도 확인될 때
  - 선택적 반영: 브랜드 특성과 일부만 맞거나, 한 축에서만 확인될 때
  - 신중 검토: 브랜드 특성과 충돌하거나 근거가 약할 때
- 상품 아이템, 컬러, 소재, 실루엣/디테일 수준까지 구체화하세요.
- 3개의 핵심 상품기획 방향을 제안하세요. 세 방향은 서로 다른 역할을 맡게 하세요.
  (예: 핵심 베이직 강화 / 시즌 트렌드 포인트 / 컬러 및 소재 업데이트)
- 같은 아이템을 여러 방향에 반복하지 마세요.

[근거 규칙]
- 추천하는 아이템, 컬러, 소재, 실루엣은 제공된 Signal에서 확인되는 것만 사용하세요.
  Signal에 없는 소재명이나 컬러명을 일반 상식으로 덧붙이지 마세요.
- 각 방향의 evidence_ids에는 그 방향의 근거가 된 Signal의 문서 번호를 그대로 적으세요.
- 추천 컬러가 있으면 그 컬러의 근거가 된 Color Signal 번호(C로 시작)를 반드시 포함하세요.
- 브랜드와 방향에 맞는 컬러 및 소재가 Signal에 없으면 해당 칸을 비우고, caution에 이유를 적으세요.

[신뢰도 규칙]
- 신뢰도 '낮음'인 축에 주로 의존하는 방향은 '적극 반영'으로 분류하지 마세요.
- 확인하지 못한 측면과 관련된 추천을 할 때는 근거가 약하다는 점을 caution에 적으세요.

[요청 반영]
- 요청 프로필의 타깃 고객, 중점 상품군, 조건을 반영하세요.

[제약]
- 제공된 Signal 밖의 기업 내부 판매 데이터를 상상하지 마세요.
- 확정적인 미래 예측처럼 표현하지 마세요.
- 결과는 한국어로 작성하고, 모든 문장은 '~다'체 평서문으로 쓰세요.
""",
    ),
    (
        "human",
        """
[사용자 입력]
시즌: {season}
브랜드: {brand}
상품기획 요청: {planning_request}

[요청 프로필]
{profile}

[Research Signals]
{signals}
{revision_block}
위 Signal을 함께 분석하여 {brand}의 {season} 상품기획 방향을 작성하세요.
""",
    ),
])



def revision_block(state: TrendState) -> str:
    # 재작성 모드일 때만 이전 결과와 Critic 피드백을 넣는다
    critique = state.get("critique")
    report = state.get("report")
    if not critique or critique.passed or not report:
        return ""

    issues = "\n".join(
        f"- [{issue.severity}/{issue.type}] {issue.target}: {issue.detail} → 수정 방법: {issue.fix}"
        for issue in critique.issues
    )
    return f"""
[재작성 모드]
아래는 이전에 작성한 결과와 검토자가 지적한 문제입니다.
- major 문제는 반드시 해결하세요. 근거가 없다고 지적된 값은 삭제하거나 Research Signals에 있는 값으로 교체하세요.
  같은 값을 그대로 다시 쓰면 안 됩니다.
- minor 문제는 가능하면 반영하세요.
- 지적받지 않은 부분은 바꾸지 말고 유지하세요.

[이전 결과]
{report.model_dump_json(indent=2)}

[검토 의견]
{issues}
"""


AXIS_OF_PREFIX = {prefix: axis for axis, prefix in AXIS_PREFIX.items()}


def axes_of(evidence_ids: list[str]) -> list[str]:
    # 근거 번호 접두어(R/C/M/B)로 근거 축을 계산
    used = {AXIS_OF_PREFIX.get(eid[:1]) for eid in evidence_ids}
    return [axis for axis in AXES if axis in used]


def strategy_writer(state: TrendState) -> dict:
    results = state["research_results"]
    if not any(result.documents for result in results.values()):
        # 4개 축 모두 자료가 없으면 전략을 쓸 근거가 없다 (대부분 API Key 및 네트워크 문제)
        raise RuntimeError("모든 Research 축에서 자료를 확보하지 못했습니다. Tavily API Key와 네트워크를 확인하세요.")

    block = revision_block(state)
    is_revision = bool(block)

    report = require(
        strategy_model.invoke(
            strategy_prompt.format_messages(
                season=state["season"],
                brand=state["brand"],
                planning_request=state["planning_request"],
                profile=profile_to_text(state["request_profile"]),
                signals=all_signals_text(state["research_results"]),
                revision_block=block,
            )
        ),
        "Strategy Writer",
    )

    # 근거 축은 LLM이 적은 값 대신 근거 번호로 다시 계산한다
    for direction in report.product_directions:
        direction.signal_sources = axes_of(direction.evidence_ids)

    revision_count = state.get("revision_count", 0) + (1 if is_revision else 0)
    message = f"{revision_count}차 재작성" if is_revision else "초안 작성"
    message += ": " + " / ".join(
        f"{direction.direction_name}({direction.adoption_level})"
        for direction in report.product_directions
    )

    return {
        "report": report,
        "revision_count": revision_count,
        "trace": [trace_event("strategy", message)],
    }

critic_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
당신은 SPA 브랜드 상품기획 리포트를 검토하는 시니어 MD입니다.
작성된 상품기획 방향이 근거 Signal과 요청에 맞는지 검토하고, 문제를 찾아 issues로 보고하세요.

[검사 항목]
- grounding: 추천 아이템, 컬러, 소재, 실루엣/디테일이 Research Signals에 실제로 등장하는가.
  Runway, Color, Market, Brand 중 어느 Signal에든 등장하면 근거가 있는 것으로 봅니다.
  (예: Brand Profile에 나온 브랜드 고유 소재는 근거가 있다.)
  어느 Signal에도 없는 컬러, 소재, 아이템이 추천되었다면 major입니다.
- brand_fit: 적용 수준(적극 반영/선택적 반영/신중 검토)이 Brand Profile과 모순되지 않는가.
- request_fit: 요청 프로필의 타깃 고객, 중점 상품군, 조건이 반영되었는가.
- overclaim: 전망을 확정된 사실처럼 표현했는가.
- confidence: 신뢰도 '낮음'인 축에 주로 의존하는 방향을 '적극 반영'으로 분류했는가.

[심각도 기준]
- major는 다음 경우에만 사용하세요.
  - grounding: Signal에 없는 아이템, 컬러, 소재, 실루엣이 추천됨
  - confidence: 신뢰도 '낮음' 축에 주로 의존하는 방향이 '적극 반영'으로 분류됨
  - brand_fit / request_fit: Brand Profile이나 요청 프로필과 정면으로 모순됨 (예: 베이직 중심 브랜드에 실험적 실루엣을 '적극 반영')
- 가격, 공정, 생산 가능성에 대한 추정, 더 나은 대안이 있다는 의견, 표현 개선은 minor로 보고하세요.
  기업 내부 데이터 없이 판단하기 어려운 내용이기 때문입니다.

[보고 규칙]
- target은 direction_1.recommended_colors처럼 위치를 적으세요. (방향 번호는 1부터)
- fix에는 어떻게 고쳐야 하는지 구체적으로 적으세요.
- 문제가 없으면 issues를 빈 리스트로 두세요. 문제를 억지로 만들지 마세요.
- 한국어로 작성하세요.
""",
    ),
    (
        "human",
        """
[요청 프로필]
{profile}

[Research Signals]
{signals}

[검토할 상품기획 방향]
{directions}
{previous_block}""",
    ),
])



# 추천 필드별로 근거가 될 수 있는 Signal (축, 필드)
FIELD_SIGNALS = {
    "recommended_items": [("runway", "key_items"), ("market", "trending_styles"), ("brand", "core_categories")],
    "recommended_colors": [("color", "key_colors")],
    "recommended_materials": [("runway", "materials")],
}
FIELD_LABELS = {
    "recommended_items": "추천 아이템",
    "recommended_colors": "추천 컬러",
    "recommended_materials": "추천 소재",
}


def signal_available(results: dict[str, AxisResult], field: str) -> bool:
    # 이 추천 필드의 근거가 될 Signal 값이 하나라도 있는가
    return any(
        axis in results and getattr(results[axis].signal, name)
        for axis, name in FIELD_SIGNALS[field]
    )


def rule_check(state: TrendState) -> list[Issue]:
    # LLM 없이 확인할 수 있는 형식 및 근거 번호 검사 (같은 입력에는 항상 같은 판정)
    report: ProductStrategyReport = state["report"]
    results = state["research_results"]
    known_ids = {doc["doc_id"] for result in results.values() for doc in result.documents}
    issues = []

    if len(report.product_directions) != 3:
        issues.append(Issue(
            target="product_directions",
            type="format",
            severity="major",
            detail=f"상품기획 방향이 {len(report.product_directions)}개입니다.",
            fix="상품기획 방향을 정확히 3개로 작성하세요.",
        ))

    seen_items: dict[str, int] = {}

    for index, direction in enumerate(report.product_directions, start=1):
        sources = axes_of(direction.evidence_ids)

        # 근거 번호 존재 여부
        if not direction.evidence_ids:
            issues.append(Issue(
                target=f"direction_{index}.evidence_ids",
                type="grounding",
                severity="major",
                detail="근거 문서 번호가 없습니다.",
                fix="Signal에 표시된 근거 문서 번호를 evidence_ids에 적으세요.",
            ))
        unknown = [eid for eid in direction.evidence_ids if eid not in known_ids]
        if unknown:
            issues.append(Issue(
                target=f"direction_{index}.evidence_ids",
                type="grounding",
                severity="major",
                detail=f"존재하지 않는 근거 번호가 있습니다: {', '.join(unknown)}",
                fix="Signal에 실제로 표시된 문서 번호만 사용하세요.",
            ))

        # 추천 컬러가 있는데 Color 근거 번호가 없는 경우 (Color Signal이 있을 때만)
        if direction.recommended_colors and "color" not in sources and signal_available(results, "recommended_colors"):
            issues.append(Issue(
                target=f"direction_{index}.evidence_ids",
                type="grounding",
                severity="major",
                detail="추천 컬러가 있지만 Color 근거 번호(C)가 없습니다.",
                fix="추천 컬러의 근거가 된 Color Signal 번호(C)를 evidence_ids에 추가하세요.",
            ))

        # 추천 필드가 비어 있는 경우 (근거가 될 Signal이 있을 때만 문제로 본다)
        # 아이템은 상품기획의 핵심이라 major, 컬러 및 소재는 브랜드에 맞는 신호가 없을 수 있어 minor
        for field, label in FIELD_LABELS.items():
            if not getattr(direction, field) and signal_available(results, field):
                issues.append(Issue(
                    target=f"direction_{index}.{field}",
                    type="format",
                    severity="major" if field == "recommended_items" else "minor",
                    detail=f"{label}가 비어 있습니다.",
                    fix=f"Signal에 근거한 {label}를 작성하거나, 비워 둔 이유를 caution에 적으세요.",
                ))

        # 적극 반영인데 시장 및 브랜드 근거가 없는 경우 (minor)
        if direction.adoption_level == "적극 반영" and not ({"market", "brand"} & set(sources)):
            issues.append(Issue(
                target=f"direction_{index}.adoption_level",
                type="brand_fit",
                severity="minor",
                detail="적극 반영이지만 Market 및 Brand 근거 번호가 없습니다.",
                fix="Market 또는 Brand 근거를 추가하거나 적용 수준을 선택적 반영으로 낮추세요.",
            ))

        # 같은 아이템이 여러 방향에 반복되는 경우 (minor)
        for item in direction.recommended_items:
            key = item.strip()
            if key in seen_items and seen_items[key] != index:
                issues.append(Issue(
                    target=f"direction_{index}.recommended_items",
                    type="format",
                    severity="minor",
                    detail=f"'{key}'가 direction_{seen_items[key]}에도 있습니다.",
                    fix="방향마다 서로 다른 아이템을 제안하세요.",
                ))
            seen_items.setdefault(key, index)

    return issues


def directions_to_text(report: ProductStrategyReport) -> str:
    return json.dumps(
        [direction.model_dump() for direction in report.product_directions],
        ensure_ascii=False,
        indent=2,
    )


def previous_issues_block(state: TrendState) -> str:
    # 재작성 결과를 검토할 때는 이전 major 지적을 함께 보여주고, 해결 여부를 먼저 확인하게 한다
    critique = state.get("critique")
    if not critique or critique.passed:
        return ""
    majors = [issue for issue in critique.issues if issue.severity == "major"]
    if not majors:
        return ""
    lines = "\n".join(f"- {issue.target}: {issue.detail}" for issue in majors)
    return f"""
[이전 검토의 major 지적]
{lines}

이번 결과는 위 지적을 반영해 재작성한 것입니다.
먼저 위 지적이 해결되었는지 확인하고, 해결되지 않았다면 다시 major로 보고하세요.
새로운 major는 심각도 기준에 명확히 해당할 때만 보고하세요.
"""


def strategy_critic(state: TrendState) -> dict:
    rule_issues = rule_check(state)

    try:
        review = require(
            critic_model.invoke(
                critic_prompt.format_messages(
                    profile=profile_to_text(state["request_profile"]),
                    signals=all_signals_text(state["research_results"]),
                    directions=directions_to_text(state["report"]),
                    previous_block=previous_issues_block(state),
                )
            ),
            "Strategy Critic",
        )
        llm_issues, llm_reviewed = review.issues, True
    except Exception:
        # LLM 검토가 실패하면 규칙 검사 결과만으로 판정한다
        llm_issues, llm_reviewed = [], False

    issues = rule_issues + llm_issues
    major = [issue for issue in issues if issue.severity == "major"]
    critique = Critique(passed=not major, issues=issues, llm_reviewed=llm_reviewed)

    if critique.passed:
        decision = "통과"
    elif state.get("revision_count", 0) < MAX_REVISIONS:
        decision = "재작성 요청"
    else:
        decision = "수정 한도 도달, 남은 문제를 리포트에 표시"

    message = f"검토: major {len(major)}건, minor {len(issues) - len(major)}건 → {decision}"
    if not llm_reviewed:
        message += " [LLM 검토 실패, 규칙 검사만 적용]"
    if major:
        message += " (" + "; ".join(f"{issue.target}: {issue.detail}" for issue in major) + ")"
    return {"critique": critique, "trace": [trace_event("critic", message)]}

def route_after_critic(state: TrendState) -> Literal["strategy", "report"]:
    if state["critique"].passed:
        return "report"
    if state.get("revision_count", 0) < MAX_REVISIONS:
        return "strategy"
    return "report"

def collect_references(results: dict[str, AxisResult], cited_ids: set[str]) -> tuple[list[dict], dict[str, int]]:
    # 리포트에서 실제로 인용한 문서만 참고문헌으로 모으고, 문서 번호 → 참조 번호 매핑을 만든다
    references: list[dict] = []
    url_to_number: dict[str, int] = {}
    id_to_number: dict[str, int] = {}

    for axis in AXES:
        if axis not in results:
            continue
        for doc in results[axis].documents:
            if doc["doc_id"] not in cited_ids:
                continue
            url = doc["url"]
            if url in url_to_number:
                number = url_to_number[url]
            else:
                number = len(references) + 1
                url_to_number[url] = number
                references.append({**doc, "number": number, "category": AXIS_LABELS[axis]})
            id_to_number[doc["doc_id"]] = number

    return references, id_to_number


def citation_text(evidence_ids: list[str], id_to_number: dict[str, int]) -> str:
    numbers = sorted({id_to_number[eid] for eid in evidence_ids if eid in id_to_number})
    return " ".join(f"[{number}]" for number in numbers) if numbers else "근거 자료 없음"


def signal_evidence_ids(result: AxisResult) -> list[str]:
    ids = []
    for name in type(result.signal).model_fields:
        value = getattr(result.signal, name)
        for item in value if isinstance(value, list) else [value]:
            ids.extend(item.evidence_ids)
    return ids


def signal_bullets(result: AxisResult, id_to_number: dict[str, int]) -> str:
    # Extract 결과를 LLM 없이 그대로 출력 (근거 번호가 붙은 문장은 모두 Signal 원문)
    lines = []
    for name in type(result.signal).model_fields:
        value = getattr(result.signal, name)
        for item in value if isinstance(value, list) else [value]:
            if item.evidence_ids:
                lines.append(
                    f"- **{SIGNAL_FIELD_LABELS.get(name, name)}** {item.name}: {item.description} "
                    f"{citation_text(item.evidence_ids, id_to_number)}"
                )
    return "\n".join(lines) or "- 확인된 신호 없음"


def references_markdown(references: list[dict]) -> str:
    if not references:
        return "사용된 자료가 없다."
    lines = []
    for item in references:
        date_text = f", {item['published_date']}" if item["published_date"] else ""
        lines.append(f"{item['number']}. [{item['title']}]({item['url']}) — {item['category']}{date_text}")
    return "\n".join(lines)


def data_quality_markdown(results: dict[str, AxisResult]) -> str:
    rows = [
        "| Research | 신뢰도 | Signal 추출에 사용한 문서 | 검색 시도 | 확인하지 못한 측면 |",
        "|---|---|---|---|---|",
    ]
    for axis in AXES:
        result = results[axis]
        rows.append(
            f"| {AXIS_LABELS[axis]} | {result.confidence} | {len(result.documents)}건 | "
            f"{result.attempts}회 | {', '.join(result.data_gaps) or '없음'} |"
        )
    rows.append("")
    rows.append("*신뢰도는 유효 문서 수와 필수 측면 확인 여부로 코드가 계산한 자료 충분도이며, 출처의 공신력은 반영하지 않는다.*")
    return "\n".join(rows)


def review_notes_markdown(critique: Critique, revision_count: int) -> str:
    if not critique.llm_reviewed:
        prefix = "*Critic의 LLM 검토가 실패해 규칙 검사만 적용했다.*\n\n"
    else:
        prefix = ""
    return prefix + review_body(critique, revision_count)


def review_body(critique: Critique, revision_count: int) -> str:
    if critique.passed:
        text = f"Critic 검토를 통과했다. (재작성 {revision_count}회)"
        minor = [issue for issue in critique.issues if issue.severity == "minor"]
        if minor:
            text += "\n\n참고 의견:\n" + "\n".join(f"- {issue.target}: {issue.detail}" for issue in minor)
        return text

    lines = [f"재작성 {revision_count}회 후에도 해결되지 않은 문제가 있어 **검토가 필요하다.**", ""]
    for issue in critique.issues:
        if issue.severity == "major":
            lines.append(f"- **{issue.target}** ({issue.type}): {issue.detail}")
    return "\n".join(lines)


def trace_markdown(trace: list[dict]) -> str:
    # 병렬 실행으로 섞인 기록을 단계 및 축 순서로 정리
    lines = []
    for event in trace:
        if event["step"] in ("normalizer", "planner"):
            lines.append(f"- **{event['step'].capitalize()}**: {event['message']}")

    for axis in AXES:
        events = [event for event in trace if event["axis"] == axis]
        if events:
            lines.append(f"- **{AXIS_LABELS[axis]}**")
            lines.extend(f"  - {event['message']}" for event in events)

    for event in trace:
        if event["step"] in ("strategy", "critic"):
            lines.append(f"- **{event['step'].capitalize()}**: {event['message']}")
    return "\n".join(lines)


def report_builder(state: TrendState) -> dict:
    report: ProductStrategyReport = state["report"]
    results = state["research_results"]

    cited_ids = {eid for result in results.values() for eid in signal_evidence_ids(result)}
    cited_ids |= {eid for direction in report.product_directions for eid in direction.evidence_ids}
    references, id_to_number = collect_references(results, cited_ids)

    insights = [
        ("runway", report.runway_insight),
        ("color", report.color_insight),
        ("market", report.current_market_insight),
        ("brand", report.brand_insight),
    ]
    research_sections = [
        f"### {index}. {AXIS_LABELS[axis]} (신뢰도: {results[axis].confidence})\n\n"
        f"**요약** {text}\n\n"
        f"{signal_bullets(results[axis], id_to_number)}"
        for index, (axis, text) in enumerate(insights, start=1)
    ]

    direction_blocks = []
    for index, direction in enumerate(report.product_directions, start=1):
        direction_blocks.append(
            f"### Strategy {index}. {direction.direction_name}\n\n"
            f"| 항목 | 내용 |\n"
            f"|---|---|\n"
            f"| **적용 수준** | {direction.adoption_level} |\n"
            f"| **추천 아이템** | {', '.join(direction.recommended_items)} |\n"
            f"| **추천 컬러** | {', '.join(direction.recommended_colors)} |\n"
            f"| **추천 소재** | {', '.join(direction.recommended_materials)} |\n"
            f"| **실루엣 / 디테일** | {', '.join(direction.silhouette_or_detail)} |\n"
            f"| **근거 축** | {', '.join(AXIS_LABELS[source] for source in direction.signal_sources)} |\n\n"
            f"**전략 판단 근거**  \n{direction.rationale}\n\n"
            f"**적용 시 고려사항**  \n{direction.caution}\n\n"
            f"*근거 자료: {citation_text(direction.evidence_ids, id_to_number)}*"
        )

    final_markdown = f"""
# Fashion Trend & Product Strategy Report

> **Season:** {state["season"]}
> **Brand:** {state["brand"]}
> **Planning Request:** {state["planning_request"]}

---

## 1. Executive Summary

{report.executive_summary}

---

## 2. Research Findings

네 Research 축에서 검증 및 구조화한 Signal이다. **요약**은 Strategy 단계에서 작성한 해석이고, 그 아래 목록은 Signal 추출 결과를 그대로 옮긴 것이다. 각 항목의 근거는 리포트 마지막 References에서 번호로 확인할 수 있다.

{(chr(10) * 2).join(research_sections)}

---

## 3. Integrated Analysis

{report.signal_connection}

---

## 4. Strategic Product Directions

{(chr(10) * 2).join(direction_blocks)}

---

## 5. Management Considerations

{report.final_note}

---

## 6. Data Quality

{data_quality_markdown(results)}

---

## 7. Review Notes

{review_notes_markdown(state["critique"], state.get("revision_count", 0))}

---

## 8. Agent Trace

{trace_markdown(state.get("trace", []))}

---

## References

{references_markdown(references)}

---

### Report Scope

본 리포트는 공개 웹 검색을 통해 수집하고 Agent가 검증한 외부 정보를 기반으로 작성되었다.
실제 상품기획 의사결정에서는 판매 실적, 재고, 원가, 마진, 고객 데이터 등 기업 내부 데이터와 함께 검토해야 한다.
"""
    return {"final_markdown": final_markdown}

builder = StateGraph(TrendState)

builder.add_node("normalizer", input_normalizer)
builder.add_node("planner", research_planner)
builder.add_node("research", run_research)
builder.add_node("strategy", strategy_writer, retry_policy=LLM_RETRY)
builder.add_node("critic", strategy_critic)  # LLM 실패는 노드 안에서 처리
builder.add_node("report", report_builder)

builder.add_edge(START, "normalizer")
builder.add_edge("normalizer", "planner")
builder.add_conditional_edges("planner", dispatch_research, ["research"])
builder.add_edge("research", "strategy")
builder.add_edge("strategy", "critic")
builder.add_conditional_edges("critic", route_after_critic, ["strategy", "report"])
builder.add_edge("report", END)

trend_agent = builder.compile()



def analyze_product_strategy(season: str, brand: str, planning_request: str) -> dict:
    # 전체 Agent 실행. 반환값은 최종 State (final_markdown, report, research_results, trace 등)
    return trend_agent.invoke(
        {
            "season": season,
            "brand": brand,
            "planning_request": planning_request,
        },
        config={"recursion_limit": RECURSION_LIMIT},
    )
