from fastapi.testclient import TestClient

from app.google.auth import (
    GOOGLE_CALENDAR_REAUTH_ENDPOINT,
    GOOGLE_CALENDAR_SERVICE_ID,
    GOOGLE_CALENDAR_SERVICE_NAME,
    ServiceAuthRequiredError,
)
from app.main import app
import app.main as main_module


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_agent_chat_requires_message() -> None:
    client = TestClient(app)
    response = client.post("/agent/chat", json={"context": {"x": 1}})
    assert response.status_code == 400
    assert response.json() == {"detail": "message is required"}


def test_agent_chat_with_mocked_agent(monkeypatch) -> None:
    async def fake_run_agent_chat(*, llm_client, message, context):
        assert llm_client is main_module.llm_client
        assert message == "hello"
        assert context == {"contract_version": "v1"}
        return {
            "result_type": "calendar_events",
            "action": "retrieve",
            "summary": {"calendar_id": "primary", "events_found_count": 0},
            "events": [],
            "meta": {
                "default_calendar_id": "primary",
                "current_datetime_utc": "2026-01-01T00:00:00+00:00",
                "current_datetime_local": "2025-12-31T19:00:00-05:00",
                "query": "hello",
            },
            "tool_results": [],
        }

    monkeypatch.setattr(main_module, "run_agent_chat", fake_run_agent_chat)
    client = TestClient(app)
    response = client.post(
        "/agent/chat",
        json={"message": "hello", "context": {"contract_version": "v1"}},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "retrieve"


def test_calendar_events_serializes_service_auth_error(monkeypatch) -> None:
    def fake_list_upcoming_events(*args, **kwargs):
        raise ServiceAuthRequiredError(
            service=GOOGLE_CALENDAR_SERVICE_ID,
            service_display_name=GOOGLE_CALENDAR_SERVICE_NAME,
            reauth_endpoint=GOOGLE_CALENDAR_REAUTH_ENDPOINT,
            reason="token_revoked_or_expired",
            message="Re-authorize Google Calendar.",
        )

    monkeypatch.setattr(main_module, "list_upcoming_events", fake_list_upcoming_events)
    client = TestClient(app)
    response = client.get("/calendar/events")
    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "service_auth_required"
    assert payload["service"] == GOOGLE_CALENDAR_SERVICE_ID
    assert payload["reauth_endpoint"] == GOOGLE_CALENDAR_REAUTH_ENDPOINT
