import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pydantic import TypeAdapter
from agent.trendweave import engine
from agent.trendweave.config import DEFAULT_BRAND

log = logging.getLogger(__name__)


class ReportService:
    def __init__(self, store, workers=1, runner=None):
        self.store = store
        self.runner = runner or self.run_graph
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='trendweave')
        # Bound both running and queued jobs, rather than an unbounded executor queue.
        self.slots = threading.BoundedSemaphore(workers * 3)

    @staticmethod
    def run_graph(request, emit):
        final = None
        planning_request = request['planning_request']
        if request.get('report_direction'):
            planning_request += '\n\n보고서 방향: ' + request['report_direction']
        for mode, payload in engine.trend_agent.stream(
                {**request, 'brand': DEFAULT_BRAND, 'planning_request': planning_request},
                config={'recursion_limit': engine.RECURSION_LIMIT},
                stream_mode=['custom', 'values']):
            if mode == 'custom':
                # Provider errors may contain URLs or credentials: retain detailed
                # exception logs locally, expose only an actionable generic message.
                payload = dict(payload)
                if payload.get('step') == 'error':
                    payload['message'] = '이 분야의 자료 수집에 실패하여 낮은 신뢰도로 진행합니다.'
                emit(payload)
            else:
                final = payload
        if not final or not final.get('final_markdown'):
            raise RuntimeError('보고서가 생성되지 않았습니다.')
        return TypeAdapter(dict).dump_python(final, mode='json')

    def submit(self, request, preview_id=None):
        request = {**request, 'brand': DEFAULT_BRAND}
        if not self.slots.acquire(blocking=False):
            raise OverflowError('대기 중인 분석이 많습니다. 잠시 후 다시 시도해 주세요.')
        try:
            report_id = self.store.create(request, preview_id=preview_id)
            self.executor.submit(self._run, report_id, request)
            return report_id
        except Exception:
            self.slots.release()
            raise

    def _run(self, report_id, request):
        try:
            self.store.running(report_id)
            result = self.runner(request, lambda event: self.store.event(report_id, event))
            self.store.complete(report_id, result)
        except Exception:
            log.exception('Report generation failed: %s', report_id)
            self.store.fail(report_id, '분석을 완료하지 못했습니다. API 키·모델·호출 한도와 네트워크를 확인한 뒤 다시 시도해 주세요.')
        finally:
            self.slots.release()

    def close(self):
        self.executor.shutdown(wait=True)
