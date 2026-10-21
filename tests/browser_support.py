"""Shared deterministic proposal data for UI-only browser tests."""
def preview_response(request):
    data=request.post_data_json
    axes=data.get('selected_axes',['runway','color','market','brand'])
    season=(data.get('runway_season') or '2027 S/S').replace(' FW',' F/W') if 'runway' in axes else None
    return {'preview_id':'test-preview','status':'pending','runway_season':season,'brand':data.get('brand','ZARA'),
            'planning_request':data['planning_request'],
            'report_type':data.get('report_type','product_strategy'),'selected_axes':axes,
            'axis_reasons':{},'domain_status':'fashion',
            'report_direction':data.get('report_direction','사용자 요청을 반영한 ZARA 4축 상품기획 방향 3개를 제안한다.'),
            'reason':'질문 조건에 맞춘 조사 계획을 제안한다.',
            'requested_at':'2026-10-21T10:30:00+09:00'}
