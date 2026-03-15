"""
Official sports data connectors (free, no API key required).

Providers
---------
ESPN (unofficial)   — MLB, NFL, NBA, NHL, MLS, WNBA, UFC, PGA, college
MLB Stats API       — MLB schedule fallback
Jolpica-F1          — Formula 1 race calendar
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone as tz
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
API_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"

SPORTS_RESULT_CACHE_TTL = 300.0
_SPORTS_RESULT_CACHE: Dict[str, tuple] = {}

# ---------------------------------------------------------------------------
# League configuration
# ---------------------------------------------------------------------------
LEAGUE_CONFIG: Dict[str, Dict[str, Any]] = {
    "mlb": {
        "espn_sport": "baseball",
        "espn_league": "mlb",
        "game_hours": 3.0,
        "type": "team",
        "espn_url": "mlb",
    },
    "nfl": {
        "espn_sport": "football",
        "espn_league": "nfl",
        "game_hours": 3.5,
        "type": "team",
        "espn_url": "nfl",
    },
    "nba": {
        "espn_sport": "basketball",
        "espn_league": "nba",
        "game_hours": 2.5,
        "type": "team",
        "espn_url": "nba",
    },
    "nhl": {
        "espn_sport": "hockey",
        "espn_league": "nhl",
        "game_hours": 2.5,
        "type": "team",
        "espn_url": "nhl",
    },
    "mls": {
        "espn_sport": "soccer",
        "espn_league": "usa.1",
        "game_hours": 2.0,
        "type": "team",
        "espn_url": "soccer",
    },
    "wnba": {
        "espn_sport": "basketball",
        "espn_league": "wnba",
        "game_hours": 2.0,
        "type": "team",
        "espn_url": "wnba",
    },
    "ufc": {
        "espn_sport": "mma",
        "espn_league": "ufc",
        "game_hours": 5.0,
        "type": "event",
        "espn_url": "mma",
    },
    "pga": {
        "espn_sport": "golf",
        "espn_league": "pga",
        "game_hours": 8.0,
        "type": "event",
        "espn_url": "golf",
    },
    "f1": {
        "game_hours": 2.0,
        "type": "calendar",
    },
    "ncaaf": {
        "espn_sport": "football",
        "espn_league": "college-football",
        "game_hours": 3.5,
        "type": "team",
        "espn_url": "college-football",
    },
    "ncaab": {
        "espn_sport": "basketball",
        "espn_league": "mens-college-basketball",
        "game_hours": 2.0,
        "type": "team",
        "espn_url": "mens-college-basketball",
    },
}

# Longer/more-specific keywords first to avoid partial-match shadowing.
LEAGUE_KEYWORDS: List[Tuple[str, str]] = [
    ("major league baseball", "mlb"),
    ("major league soccer", "mls"),
    ("national football league", "nfl"),
    ("national basketball association", "nba"),
    ("national hockey league", "nhl"),
    ("college football", "ncaaf"),
    ("ncaa football", "ncaaf"),
    ("college basketball", "ncaab"),
    ("ncaa basketball", "ncaab"),
    ("march madness", "ncaab"),
    ("mixed martial arts", "ufc"),
    ("ultimate fighting", "ufc"),
    ("formula one", "f1"),
    ("formula 1", "f1"),
    ("grand prix", "f1"),
    ("pga tour", "pga"),
    ("mlb", "mlb"),
    ("nfl", "nfl"),
    ("nba", "nba"),
    ("nhl", "nhl"),
    ("mls", "mls"),
    ("wnba", "wnba"),
    ("ufc", "ufc"),
    ("mma", "ufc"),
    ("pga", "pga"),
    ("f1", "f1"),
    ("ncaaf", "ncaaf"),
    ("ncaab", "ncaab"),
    ("baseball", "mlb"),
    ("hockey", "nhl"),
    ("golf", "pga"),
]

NOISE_WORDS = frozenset(
    "schedule games game events event calendar add all the for in my to of "
    "this next upcoming season regular preseason postseason "
    "january february march april may june july august september october "
    "november december jan feb mar apr jun jul aug sep oct nov dec "
    "2024 2025 2026 2027 2028 week month year today tomorrow weekend".split()
)

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
_espn_team_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_mlb_team_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None
TEAM_CACHE_TTL = 86400.0  # 24 h

# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _localize_events(
    events: List[Dict[str, Any]], target_tz: str
) -> List[Dict[str, Any]]:
    """Convert UTC event start/end to the user's local timezone."""
    try:
        zone = ZoneInfo(target_tz)
    except (KeyError, Exception):
        return events

    localized: List[Dict[str, Any]] = []
    for evt in events:
        evt = dict(evt)
        for field in ("start", "end"):
            raw = evt.get(field)
            if not raw:
                continue
            dt = _parse_dt(raw)
            if dt is None:
                continue
            local_dt = dt.astimezone(zone)
            evt[field] = local_dt.isoformat()
        evt["timezone"] = target_tz
        localized.append(evt)
    return localized


def _fuzzy_score(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    q = query.lower().strip()
    c = candidate.lower().strip()
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.85
    return SequenceMatcher(None, q, c).ratio()


def _tokenize_meaningful(text: str) -> List[str]:
    parts = re.findall(r"[a-z0-9]+", text.lower())
    return [p for p in parts if p and p not in NOISE_WORDS and len(p) > 1]


def _team_match_score(team_query: str, team: Dict[str, Any]) -> Tuple[float, float]:
    """
    Return (token_recall, fuzzy_score) for a team candidate.

    token_recall is the fraction of query tokens present in at least one
    canonical team name representation. This avoids false positives such as
    matching "Atlanta United" to "Atlanta Braves" due to city-only overlap.
    """
    query_tokens = set(_tokenize_meaningful(team_query))
    if not query_tokens:
        return (0.0, 0.0)

    candidate_fields = [
        str(team.get("displayName", "")),
        str(team.get("shortDisplayName", "")),
        str(team.get("abbreviation", "")),
        str(team.get("nickname", "")),
        str(team.get("location", "")),
        f"{team.get('location', '')} {team.get('nickname', '')}",
    ]
    candidate_fields = [c.strip() for c in candidate_fields if c and str(c).strip()]
    if not candidate_fields:
        return (0.0, 0.0)

    best_fuzzy = max(_fuzzy_score(team_query, c) for c in candidate_fields)

    combined_tokens: set[str] = set()
    for candidate in candidate_fields:
        combined_tokens.update(_tokenize_meaningful(candidate))
    token_overlap = len(query_tokens.intersection(combined_tokens))
    token_recall = token_overlap / max(1, len(query_tokens))
    return (token_recall, best_fuzzy)


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz.utc)
        return dt
    except ValueError:
        return None


def _filter_events_by_range(
    events: List[Dict[str, Any]],
    start_dt: datetime,
    end_dt: datetime,
) -> List[Dict[str, Any]]:
    s = start_dt if start_dt.tzinfo else start_dt.replace(tzinfo=tz.utc)
    e = end_dt if end_dt.tzinfo else end_dt.replace(tzinfo=tz.utc)
    out: List[Dict[str, Any]] = []
    for evt in events:
        evt_start = _parse_dt(evt.get("start", ""))
        if evt_start and s <= evt_start <= e:
            out.append(evt)
    return out


def _extract_team_query(subject: str, league_keyword: str) -> str:
    text = subject.lower().replace(league_keyword, " ")
    words = text.split()
    meaningful = [w for w in words if w not in NOISE_WORDS and len(w) > 1]
    return " ".join(meaningful).strip()


# ===================================================================
# ESPN provider
# ===================================================================

def _espn_get(path: str, params: Dict[str, Any] | None = None) -> Any:
    url = f"{ESPN_BASE}/{path}"
    with httpx.Client(timeout=API_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url, params=params or {})
        resp.raise_for_status()
        return resp.json()


def _espn_fetch_teams(espn_sport: str, espn_league: str) -> List[Dict[str, Any]]:
    cache_key = f"{espn_sport}/{espn_league}"
    now = time.time()
    cached = _espn_team_cache.get(cache_key)
    if cached and (now - cached[0]) < TEAM_CACHE_TTL:
        return cached[1]

    data = _espn_get(f"{espn_sport}/{espn_league}/teams", {"limit": "200"})
    teams: List[Dict[str, Any]] = []
    for sport_block in data.get("sports", []):
        for league_block in sport_block.get("leagues", []):
            for team_entry in league_block.get("teams", []):
                team = team_entry.get("team", team_entry)
                teams.append(
                    {
                        "id": str(team.get("id", "")),
                        "displayName": team.get("displayName", ""),
                        "shortDisplayName": team.get("shortDisplayName", ""),
                        "abbreviation": team.get("abbreviation", ""),
                        "nickname": team.get("nickname", ""),
                        "location": team.get("location", ""),
                    }
                )

    _espn_team_cache[cache_key] = (now, teams)
    return teams


def _espn_resolve_team(
    team_query: str,
    espn_sport: str,
    espn_league: str,
) -> Optional[Dict[str, Any]]:
    if not team_query.strip():
        return None

    teams = _espn_fetch_teams(espn_sport, espn_league)
    best_team: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for team in teams:
        for candidate in (
            team["displayName"],
            team["shortDisplayName"],
            team["abbreviation"],
            team["nickname"],
            team["location"],
            f"{team['location']} {team['nickname']}",
        ):
            score = _fuzzy_score(team_query, candidate)
            if score > best_score:
                best_score = score
                best_team = team

    return best_team if best_score >= 0.5 else None


def _normalize_espn_event(
    evt: Dict[str, Any],
    game_hours: float,
    espn_url: str,
) -> Optional[Dict[str, Any]]:
    event_id = str(evt.get("id", ""))
    name = evt.get("name", "")
    date_str = evt.get("date", "")
    if not name or not date_str:
        return None

    start_dt = _parse_dt(date_str)
    if start_dt is None:
        return None
    end_dt = start_dt + timedelta(hours=game_hours)

    venue_str = ""
    competitions = evt.get("competitions", [])
    if competitions:
        venue = competitions[0].get("venue", {})
        if venue:
            parts = [
                p
                for p in [
                    venue.get("fullName", ""),
                    venue.get("address", {}).get("city", ""),
                    venue.get("address", {}).get("state", ""),
                ]
                if p
            ]
            venue_str = ", ".join(parts)

    season_type = ""
    st_block = evt.get("seasonType") or evt.get("season", {})
    if isinstance(st_block, dict):
        season_type = st_block.get("name", "") or st_block.get("slug", "")

    desc_parts = [p for p in [season_type, venue_str] if p]
    description = " | ".join(desc_parts) if desc_parts else None
    source_url = (
        f"https://www.espn.com/{espn_url}/game/_/gameId/{event_id}"
        if event_id
        else None
    )

    return {
        "summary": name,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "timezone": "UTC",
        "description": description,
        "source_url": source_url,
        "confidence": 0.95,
    }


def _espn_fetch_team_schedule(
    team_id: str,
    espn_sport: str,
    espn_league: str,
    start_dt: datetime,
    end_dt: datetime,
    game_hours: float,
    espn_url: str,
) -> List[Dict[str, Any]]:
    seasons = {start_dt.year}
    if end_dt.year != start_dt.year:
        seasons.add(end_dt.year)
    # Some leagues span two calendar years (e.g. NBA/NHL/NFL). Include the
    # prior season year when the requested window starts in the same year.
    if espn_sport in {"basketball", "hockey", "football"}:
        seasons.add(start_dt.year - 1)

    events: List[Dict[str, Any]] = []
    for season in sorted(seasons):
        try:
            data = _espn_get(
                f"{espn_sport}/{espn_league}/teams/{team_id}/schedule",
                {"season": str(season)},
            )
        except Exception as exc:
            logger.warning(
                "ESPN schedule fetch failed team=%s season=%s: %s",
                team_id,
                season,
                exc,
            )
            continue

        for evt in data.get("events", []):
            parsed = _normalize_espn_event(evt, game_hours, espn_url)
            if parsed:
                events.append(parsed)

    return _filter_events_by_range(events, start_dt, end_dt)


def _espn_fetch_scoreboard_range(
    espn_sport: str,
    espn_league: str,
    start_dt: datetime,
    end_dt: datetime,
    game_hours: float,
    espn_url: str,
    max_days: int = 60,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    current = start_dt.date()
    end_date = end_dt.date()
    days_fetched = 0

    while current <= end_date and days_fetched < max_days:
        date_str = current.strftime("%Y%m%d")
        try:
            data = _espn_get(
                f"{espn_sport}/{espn_league}/scoreboard",
                {"dates": date_str},
            )
        except Exception as exc:
            logger.warning("ESPN scoreboard %s failed: %s", date_str, exc)
            current += timedelta(days=1)
            days_fetched += 1
            continue

        for evt in data.get("events", []):
            parsed = _normalize_espn_event(evt, game_hours, espn_url)
            if parsed:
                events.append(parsed)

        current += timedelta(days=1)
        days_fetched += 1

    return events


def _extract_event_id_from_ref(ref: str) -> str:
    match = re.search(r"/events/(\d+)", ref or "")
    return match.group(1) if match else ""


def _normalize_espn_core_event(
    evt: Dict[str, Any],
    game_hours: float,
    espn_url: str,
    event_id_hint: str = "",
) -> Optional[Dict[str, Any]]:
    name = evt.get("name", "")
    date_str = evt.get("date", "")
    if not name or not date_str:
        return None

    start_dt = _parse_dt(date_str)
    if start_dt is None:
        return None
    end_dt = start_dt + timedelta(hours=game_hours)

    event_id = str(evt.get("id") or event_id_hint or "")
    source_url = (
        f"https://www.espn.com/{espn_url}/game/_/gameId/{event_id}"
        if event_id
        else None
    )

    description = "Regular Season"
    season_type = evt.get("seasonType")
    if isinstance(season_type, dict):
        maybe_name = season_type.get("name") or season_type.get("slug")
        if maybe_name:
            description = str(maybe_name)

    return {
        "summary": name,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "timezone": "UTC",
        "description": description,
        "source_url": source_url,
        "confidence": 0.95,
    }


def _espn_fetch_team_schedule_core(
    team_id: str,
    espn_sport: str,
    espn_league: str,
    start_dt: datetime,
    end_dt: datetime,
    game_hours: float,
    espn_url: str,
) -> List[Dict[str, Any]]:
    seasons = {start_dt.year}
    if end_dt.year != start_dt.year:
        seasons.add(end_dt.year)
    if espn_sport in {"basketball", "hockey", "football"}:
        seasons.add(start_dt.year - 1)

    events: List[Dict[str, Any]] = []
    with httpx.Client(timeout=API_TIMEOUT, follow_redirects=True) as client:
        for season in sorted(seasons):
            try:
                listing_url = (
                    "https://sports.core.api.espn.com/v2/sports/"
                    f"{espn_sport}/leagues/{espn_league}/seasons/{season}/types/1/teams/{team_id}/events"
                )
                listing = client.get(
                    listing_url,
                    params={"lang": "en", "region": "us", "limit": "200"},
                )
                listing.raise_for_status()
                listing_data = listing.json()
            except Exception as exc:
                logger.warning(
                    "ESPN core schedule list failed team=%s season=%s: %s",
                    team_id,
                    season,
                    exc,
                )
                continue

            for item in listing_data.get("items", []):
                if not isinstance(item, dict):
                    continue
                ref = str(item.get("$ref", ""))
                if not ref:
                    continue
                try:
                    evt_resp = client.get(ref)
                    evt_resp.raise_for_status()
                    evt = evt_resp.json()
                except Exception:
                    continue
                parsed = _normalize_espn_core_event(
                    evt,
                    game_hours=game_hours,
                    espn_url=espn_url,
                    event_id_hint=_extract_event_id_from_ref(ref),
                )
                if parsed:
                    events.append(parsed)

    return _filter_events_by_range(events, start_dt, end_dt)


# ===================================================================
# MLB Stats API provider (fallback)
# ===================================================================

def _mlb_get(path: str, params: Dict[str, Any] | None = None) -> Any:
    url = f"{MLB_API_BASE}/{path}"
    with httpx.Client(timeout=API_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url, params=params or {})
        resp.raise_for_status()
        return resp.json()


def _mlb_fetch_teams() -> List[Dict[str, Any]]:
    global _mlb_team_cache
    now = time.time()
    if _mlb_team_cache and (now - _mlb_team_cache[0]) < TEAM_CACHE_TTL:
        return _mlb_team_cache[1]

    data = _mlb_get("teams", {"sportId": "1"})
    teams: List[Dict[str, Any]] = []
    for team in data.get("teams", []):
        teams.append(
            {
                "id": str(team.get("id", "")),
                "name": team.get("name", ""),
                "teamName": team.get("teamName", ""),
                "abbreviation": team.get("abbreviation", ""),
                "locationName": team.get("locationName", ""),
            }
        )

    _mlb_team_cache = (now, teams)
    return teams


def _mlb_resolve_team(team_query: str) -> Optional[Dict[str, Any]]:
    if not team_query.strip():
        return None

    teams = _mlb_fetch_teams()
    best_team: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for team in teams:
        for candidate in (
            team["name"],
            team["teamName"],
            team["abbreviation"],
            team["locationName"],
            f"{team['locationName']} {team['teamName']}",
        ):
            score = _fuzzy_score(team_query, candidate)
            if score > best_score:
                best_score = score
                best_team = team

    return best_team if best_score >= 0.5 else None


def _mlb_fetch_schedule(
    team_id: str,
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    data = _mlb_get(
        "schedule",
        {"teamId": team_id, "startDate": start_date, "endDate": end_date, "sportId": "1"},
    )
    events: List[Dict[str, Any]] = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            game_date = game.get("gameDate", "")
            if not game_date:
                continue
            start_dt = _parse_dt(game_date)
            if start_dt is None:
                continue
            end_dt = start_dt + timedelta(hours=3.0)

            away = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
            home = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
            summary = f"{away} at {home}" if away and home else "MLB Game"
            venue = game.get("venue", {}).get("name", "")
            game_pk = game.get("gamePk", "")

            events.append(
                {
                    "summary": summary,
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "timezone": "UTC",
                    "description": f"Venue: {venue}" if venue else None,
                    "source_url": f"https://www.mlb.com/gameday/{game_pk}" if game_pk else None,
                    "confidence": 0.95,
                }
            )
    return events


# ===================================================================
# Jolpica-F1 provider
# ===================================================================

def _jolpica_fetch_races(
    start_dt: datetime,
    end_dt: datetime,
) -> List[Dict[str, Any]]:
    seasons = {start_dt.year}
    if end_dt.year != start_dt.year:
        seasons.add(end_dt.year)

    events: List[Dict[str, Any]] = []
    for season in sorted(seasons):
        try:
            url = f"{JOLPICA_BASE}/{season}/races/"
            with httpx.Client(timeout=API_TIMEOUT, follow_redirects=True) as client:
                resp = client.get(url, params={"limit": "100"})
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("Jolpica F1 fetch failed season=%s: %s", season, exc)
            continue

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        for race in races:
            race_name = race.get("raceName", "")
            race_date = race.get("date", "")
            race_time = race.get("time", "14:00:00Z")
            if not race_name or not race_date:
                continue

            dt_str = f"{race_date}T{race_time}"
            if not dt_str.endswith("Z") and "+" not in dt_str:
                dt_str += "Z"
            start = _parse_dt(dt_str)
            if start is None:
                continue
            end = start + timedelta(hours=2.0)

            circuit = race.get("Circuit", {})
            circuit_name = circuit.get("circuitName", "")
            loc = circuit.get("Location", {})
            loc_parts = [p for p in [circuit_name, loc.get("locality", ""), loc.get("country", "")] if p]

            events.append(
                {
                    "summary": f"F1: {race_name}",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "timezone": "UTC",
                    "description": ", ".join(loc_parts) if loc_parts else None,
                    "source_url": race.get("url") or circuit.get("url"),
                    "confidence": 0.95,
                }
            )

    return _filter_events_by_range(events, start_dt, end_dt)


# ===================================================================
# Detection
# ===================================================================

_TEAM_LEAGUE_SCAN_ORDER = ["mlb", "nfl", "nba", "nhl", "mls", "wnba", "ncaaf", "ncaab"]


def detect_sports_query(subject: str) -> Optional[Dict[str, Any]]:
    """
    Identify league and team-name portion from a natural-language subject.
    Returns ``{league, team_query, config}`` or ``None``.

    Two-pass strategy:
      1. Fast keyword scan (e.g. "mlb", "baseball", "nfl")
      2. Dynamic team resolution — strip noise words and try to match the
         remaining text against ESPN team lists for each major league.
    """
    text = subject.lower().strip()

    matched_league: Optional[str] = None
    matched_keyword: Optional[str] = None
    for keyword, league_id in LEAGUE_KEYWORDS:
        if keyword in text:
            matched_league = league_id
            matched_keyword = keyword
            break

    if matched_league is not None:
        config = LEAGUE_CONFIG.get(matched_league)
        if not config:
            return None
        team_query = _extract_team_query(subject, matched_keyword) if matched_keyword else ""
        return {"league": matched_league, "team_query": team_query, "config": config}

    # Fallback: resolve as a team name across leagues and choose the strongest
    # token-aware match (instead of first-match by scan order).
    words = text.split()
    candidate = " ".join(w for w in words if w not in NOISE_WORDS and len(w) > 1)
    if not candidate:
        return None

    query_tokens = set(_tokenize_meaningful(candidate))
    if not query_tokens:
        return None

    best_choice: Optional[Tuple[str, Dict[str, Any], Dict[str, Any], float, float]] = None
    # (league_id, config, team, token_recall, fuzzy_score)

    for league_id in _TEAM_LEAGUE_SCAN_ORDER:
        config = LEAGUE_CONFIG.get(league_id)
        if not config or config.get("type") != "team":
            continue
        espn_sport = config.get("espn_sport")
        espn_league = config.get("espn_league")
        if not espn_sport or not espn_league:
            continue
        try:
            teams = _espn_fetch_teams(espn_sport, espn_league)
        except Exception:
            continue
        if not teams:
            continue

        league_best_team: Optional[Dict[str, Any]] = None
        league_best_recall = 0.0
        league_best_fuzzy = 0.0
        for team in teams:
            token_recall, fuzzy = _team_match_score(candidate, team)
            if (token_recall, fuzzy) > (league_best_recall, league_best_fuzzy):
                league_best_recall = token_recall
                league_best_fuzzy = fuzzy
                league_best_team = team

        if league_best_team is None:
            continue
        if best_choice is None or (league_best_recall, league_best_fuzzy) > (
            best_choice[3],
            best_choice[4],
        ):
            best_choice = (
                league_id,
                config,
                league_best_team,
                league_best_recall,
                league_best_fuzzy,
            )

    if best_choice is None:
        return None

    league_id, config, team, token_recall, fuzzy_score = best_choice
    min_recall = 1.0 if len(query_tokens) >= 2 else 0.5
    min_fuzzy = 0.5
    if token_recall >= min_recall and fuzzy_score >= min_fuzzy:
        logger.info(
            "Dynamic team detection: '%s' resolved to %s (%s) token_recall=%.2f fuzzy=%.2f",
            candidate,
            team.get("displayName"),
            league_id.upper(),
            token_recall,
            fuzzy_score,
        )
        return {"league": league_id, "team_query": candidate, "config": config}

    return None


# ===================================================================
# Main entry point
# ===================================================================

def _sports_cache_key(subject: str, start_time: str | None, end_time: str | None, tz: str) -> str:
    return f"{subject.lower().strip()}|{start_time or ''}|{end_time or ''}|{tz}"


def search_sports_events(
    subject: str,
    start_time: str | None,
    end_time: str | None,
    timezone_str: str = "UTC",
) -> Optional[Dict[str, Any]]:
    """
    Search official sports APIs for event schedules.

    Returns a result dict (same shape as web-search output) or ``None``
    when the query is not sports-related.
    """
    cache_key = _sports_cache_key(subject, start_time, end_time, timezone_str)
    cached = _SPORTS_RESULT_CACHE.get(cache_key)
    if cached is not None:
        ts, data = cached
        if (time.time() - ts) < SPORTS_RESULT_CACHE_TTL:
            return data

    detection = detect_sports_query(subject)
    if detection is None:
        return None

    league: str = detection["league"]
    team_query: str = detection["team_query"]
    config: Dict[str, Any] = detection["config"]
    league_type: str = config.get("type", "team")
    game_hours: float = config.get("game_hours", 3.0)

    now = datetime.now(tz.utc)
    start_dt = _parse_dt(start_time) if start_time else now
    end_dt = _parse_dt(end_time) if end_time else (now + timedelta(days=365))
    if start_dt is None:
        start_dt = now
    if end_dt is None:
        end_dt = now + timedelta(days=365)

    started = time.perf_counter()
    events: List[Dict[str, Any]] = []
    source = "none"
    provider_errors: List[str] = []

    # --- F1: Jolpica ---
    if league == "f1":
        try:
            events = _jolpica_fetch_races(start_dt, end_dt)
            source = "jolpica_f1"
        except Exception as exc:
            provider_errors.append(f"jolpica_f1: {exc}")

    # --- Team sports: ESPN team schedule (+ MLB Stats API fallback) ---
    elif league_type == "team":
        espn_sport = config.get("espn_sport", "")
        espn_league = config.get("espn_league", "")
        espn_url = config.get("espn_url", "")

        team: Optional[Dict[str, Any]] = None
        if team_query:
            try:
                team = _espn_resolve_team(team_query, espn_sport, espn_league)
            except Exception as exc:
                provider_errors.append(f"espn_team_resolve: {exc}")

        if team:
            try:
                events = _espn_fetch_team_schedule(
                    team["id"], espn_sport, espn_league,
                    start_dt, end_dt, game_hours, espn_url,
                )
                source = "espn"
            except Exception as exc:
                provider_errors.append(f"espn_schedule: {exc}")

        # MLS team schedule endpoint can return only a short rolling window.
        # When a broad date range is requested, use ESPN core events API as a
        # fallback to capture the full season slate.
        if (
            not events or (league == "mls" and len(events) < 10 and (end_dt - start_dt).days >= 120)
        ) and team and league == "mls":
            try:
                core_events = _espn_fetch_team_schedule_core(
                    team["id"],
                    espn_sport,
                    espn_league,
                    start_dt,
                    end_dt,
                    game_hours,
                    espn_url,
                )
                if len(core_events) > len(events):
                    events = core_events
                    source = "espn_core"
            except Exception as exc:
                provider_errors.append(f"espn_core_schedule: {exc}")

        # MLB fallback/augmentation: if MLB Stats API returns more events for
        # the same window, prefer it over ESPN.
        if league == "mlb" and team_query:
            try:
                mlb_team = _mlb_resolve_team(team_query)
                if mlb_team:
                    mlb_events = _mlb_fetch_schedule(
                        mlb_team["id"],
                        start_dt.strftime("%Y-%m-%d"),
                        end_dt.strftime("%Y-%m-%d"),
                    )
                    if len(mlb_events) > len(events):
                        events = mlb_events
                        source = "mlb_statsapi"
            except Exception as exc:
                provider_errors.append(f"mlb_statsapi: {exc}")

        # No team specified — fall back to scoreboard for the whole league
        if not events and not team_query:
            try:
                events = _espn_fetch_scoreboard_range(
                    espn_sport, espn_league,
                    start_dt, end_dt, game_hours, espn_url,
                )
                source = "espn"
            except Exception as exc:
                provider_errors.append(f"espn_scoreboard: {exc}")

    # --- Event sports (UFC, golf): ESPN scoreboard ---
    elif league_type == "event":
        espn_sport = config.get("espn_sport", "")
        espn_league = config.get("espn_league", "")
        espn_url = config.get("espn_url", "")
        try:
            events = _espn_fetch_scoreboard_range(
                espn_sport, espn_league,
                start_dt, end_dt, game_hours, espn_url,
            )
            source = "espn"
        except Exception as exc:
            provider_errors.append(f"espn_scoreboard: {exc}")

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    # All providers errored and we have nothing — return None so caller
    # can fall back to web search.
    if not events and provider_errors:
        return None

    if events and timezone_str and timezone_str != "UTC":
        events = _localize_events(events, timezone_str)

    result = {
        "query": subject,
        "search_results": [],
        "documents_count": 0,
        "events": events,
        "events_count": len(events),
        "source": source,
        "detected_league": league,
        "detected_team_query": team_query or None,
        "performance": {
            "total_elapsed_ms": elapsed_ms,
            "source": "official_api",
            "provider_errors": provider_errors or None,
        },
    }

    _SPORTS_RESULT_CACHE[cache_key] = (time.time(), result)
    return result
