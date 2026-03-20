from app.agent.core import _build_reauth_required_response, _derive_action


def test_derive_action_prefers_mixed_for_edit_plus_lookup() -> None:
    tool_results = [
        {"name": "edit_calendar_events"},
        {"name": "search_calendar_events"},
    ]
    assert _derive_action(tool_results) == "mixed"


def test_derive_action_returns_create() -> None:
    assert _derive_action([{"name": "create_event"}]) == "create"


def test_build_reauth_required_response_shape() -> None:
    response = _build_reauth_required_response(
        runtime_context={
            "default_calendar_id": "primary",
            "current_datetime_utc": "2026-03-20T12:00:00+00:00",
            "current_datetime_local": "2026-03-20T08:00:00-04:00",
        },
        query="add my events",
        service="google_calendar",
        service_display_name="Google Calendar",
        reauth_endpoint="/auth/google/start",
        message="Need reauth.",
        tool_results=[{"name": "get_upcoming_events"}],
        resume_context={"resume_id": "abc"},
    )
    assert response["action"] == "reauthorization_required"
    assert response["summary"]["requires_reauth"] is True
    assert response["summary"]["service"] == "google_calendar"
    assert response["summary"]["resume_context"]["resume_id"] == "abc"
    assert response["meta"]["query"] == "add my events"
