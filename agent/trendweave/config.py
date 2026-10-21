DEFAULT_BRAND = "ZARA"

from datetime import date, timedelta

AXES = ["runway", "color", "market", "brand"]

AXIS_LABELS = {
    "runway": "Runway Research",
    "color": "Color Forecast Research",
    "market": "Current Market Research",
    "brand": "Brand Context Research",
}

AXIS_PREFIX = {"runway": "R", "color": "C", "market": "M", "brand": "B"}

AXIS_PURPOSE = {
    "runway": "다음 시즌 패션위크 및 디자이너 컬렉션에서 나타난 실루엣, 소재, 디테일, 핵심 아이템",
    "color": "다음 시즌 컬러 전망과 런웨이 컬러 팔레트",
    "market": "현재 시장과 소비자가 실제로 반응하고 있는 스타일과 소비 흐름",
    "brand": "선택한 SPA 브랜드의 포지셔닝, 타깃 고객, 핵심 상품군, 가격대, 디자인 정체성",
}

# 축별 필수 확인 측면 (충분성 판단 기준)
BASE_ASPECTS = {
    "runway": ["실루엣", "소재", "아이템"],
    "color": ["핵심 컬러", "컬러 무드"],
    "market": ["스타일 흐름", "소비자 반응"],
    "brand": ["포지셔닝", "타깃 고객", "핵심 상품군"],
}

# 축별 문서 평가 기준
GRADE_CRITERIA = {
    "runway": (
        "- 요청 시즌의 컬렉션 및 런웨이를 다루는가 (다른 시즌이면 불일치)\n"
        "- 실루엣, 소재, 아이템 등 구체적인 디자인 정보가 있는가\n"
        "- 패션 전문 매체 또는 1차 출처인가 (쇼핑몰 광고 페이지는 부적합)"
    ),
    "color": (
        "- 요청 시즌(또는 해당 연도)의 컬러 전망인가 (다른 시즌이면 불일치)\n"
        "- 구체적인 컬러명이 있는가"
    ),
    "market": (
        "- 최근 자료인가 (오늘 날짜 기준 발행일 확인)\n"
        "- 실제 소비, 판매, 스트리트 흐름을 다루는가\n"
        "- 시즌 일치 여부는 이 축에서 판단하지 않으므로 season_match는 '해당 없음'으로 둔다"
    ),
    "brand": (
        "- 분석 대상 브랜드 자체를 다루는 자료인가 (다른 브랜드 중심 자료는 부적합)\n"
        "- 포지셔닝, 타깃 고객, 상품 구성 정보가 있는가\n"
        "- 브랜드 공식 자료나 언론 기사를 우선한다. 출처가 불분명한 요약 블로그는 내용이 구체적일 때만 인정한다\n"
        "- 시즌 일치 여부는 이 축에서 판단하지 않으므로 season_match는 '해당 없음'으로 둔다"
    ),
}

# 모든 축에 공통으로 적용하는 평가 기준
COMMON_GRADE_CRITERIA = "- Pinterest 및 Instagram 등 SNS 게시물, 쇼핑몰 상품 페이지는 부적합"

# 검색 결과에서 코드로 제외할 SNS 도메인 (평가 전에 걸러 LLM 호출을 줄인다)
EXCLUDED_DOMAINS = ["pinterest.", "instagram.com", "tiktok.com", "facebook.com"]

# Runway 검색 범위를 좁힐 패션 전문 매체
FASHION_MEDIA_DOMAINS = [
    "vogue.com",
    "wwd.com",
    "businessoffashion.com",
    "harpersbazaar.com",
    "elle.com",
    "fashionista.com",
    "whowhatwear.com",
    "marieclaire.com",
    "nssmag.com",
    "hypebeast.com",
]


def search_options(axis: str, today: str) -> dict:
    # 축별 Tavily 검색 옵션
    if axis == "runway":
        return {"include_domains": FASHION_MEDIA_DOMAINS}
    if axis == "market":
        start = date.fromisoformat(today) - timedelta(days=120)
        return {"topic": "news", "start_date": start.isoformat()}
    return {}
MAX_SEARCH_ATTEMPTS = 3
MIN_VALID_DOCS = 3
MAX_REVISIONS = 2
MAX_DOCS_FOR_EXTRACT = 6
RECURSION_LIMIT = 50
