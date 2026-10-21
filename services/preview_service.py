from agent.trendweave.review import suggest_plan


class PreviewService:
    def __init__(self, store, proposer=None):
        self.store = store
        self.proposer = proposer or suggest_plan

    def propose(self, planning_request, thread_id, requested_at, **overrides):
        suggestion = self.proposer(planning_request, requested_at, **overrides)
        if isinstance(suggestion, dict):
            return {**suggestion, 'requested_at': requested_at}
        payload = {**suggestion.model_dump(), 'planning_request': planning_request,
                   'requested_at': requested_at}
        return self.store.create_preview(thread_id, payload)
