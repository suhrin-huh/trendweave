"""Shared deterministic proposal data for UI-only browser tests."""
def preview_response(request):
    return {'preview_id':'test-preview','status':'pending','season':'2027 S/S','brand':'ZARA',
            'planning_request':request.post_data_json['planning_request'],
            'report_direction':'사용자 요청을 반영한 ZARA 4축 상품기획 방향 3개를 제안한다.',
            'reason':'요청 기준 시점에 준비할 다음 봄·여름 시즌으로 제안한다.',
            'requested_at':'2026-10-21T10:30:00+09:00'}
