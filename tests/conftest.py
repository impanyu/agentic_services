import pytest

@pytest.fixture
def stub_photo_route_intent(monkeypatch):
    """Isolate route/commerce tests from real planner calls; planner behavior has its own tests."""
    from agentic_services.photo_scout import routes
    async def resolve(settings,payload):
        data=payload.model_dump()
        return {**data,'locations':[{'lat':payload.lat,'lon':payload.lon,'label':'Selected map location'}],
                'radiusMeters':payload.radius,'explanation':'Current controls',
                'preferences':payload.preferences or routes.DEFAULT_PHOTO_PREFERENCES}
    monkeypatch.setattr(routes,'resolve_intent',resolve)
