import httpx
import respx

import app.data_sources.sports as sports


@respx.mock
def test_espn_get_uses_expected_url() -> None:
    route = respx.get(
        "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams"
    ).mock(return_value=httpx.Response(200, json={"sports": []}))

    payload = sports._espn_get("baseball/mlb/teams", {"limit": "200"})
    assert route.called
    assert payload == {"sports": []}


def test_search_sports_events_returns_none_for_non_sports_queries(monkeypatch) -> None:
    monkeypatch.setattr(sports, "detect_sports_query", lambda subject: None)
    result = sports.search_sports_events("review design doc", None, None, "UTC")
    assert result is None


def test_search_sports_events_returns_none_when_provider_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        sports,
        "detect_sports_query",
        lambda subject: {
            "league": "mlb",
            "team_query": "yankees",
            "config": {
                "type": "team",
                "game_hours": 3.0,
                "espn_sport": "baseball",
                "espn_league": "mlb",
                "espn_url": "mlb",
            },
        },
    )
    monkeypatch.setattr(sports, "_espn_resolve_team", lambda *args, **kwargs: {"id": "10"})
    monkeypatch.setattr(
        sports,
        "_espn_fetch_team_schedule",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        sports,
        "_mlb_resolve_team",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = sports.search_sports_events("mlb yankees schedule", None, None, "UTC")
    assert result is None
