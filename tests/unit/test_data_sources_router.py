import app.data_sources.router as router


def test_try_official_source_returns_connector_result(monkeypatch) -> None:
    expected = {"source": "official_api", "events_count": 1}

    def fake_search(subject, start_time, end_time, timezone_str):
        assert subject == "mlb schedule"
        assert start_time == "2026-03-20T00:00:00Z"
        assert end_time == "2026-03-21T00:00:00Z"
        assert timezone_str == "America/New_York"
        return expected

    monkeypatch.setattr(router, "search_sports_events", fake_search)
    result = router.try_official_source(
        "mlb schedule",
        start_time="2026-03-20T00:00:00Z",
        end_time="2026-03-21T00:00:00Z",
        timezone_str="America/New_York",
    )
    assert result == expected


def test_try_official_source_returns_none_when_not_supported(monkeypatch) -> None:
    monkeypatch.setattr(router, "search_sports_events", lambda *args, **kwargs: None)
    result = router.try_official_source("company all-hands")
    assert result is None
