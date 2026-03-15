"""
Domain router for official data sources.

Tries registered connectors in order and returns the first successful result.
Currently registers: sports.  Extend by adding more connectors below.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.data_sources.sports import search_sports_events


def try_official_source(
    subject: str,
    start_time: str | None = None,
    end_time: str | None = None,
    timezone_str: str = "UTC",
) -> Optional[Dict[str, Any]]:
    """
    Try every registered official-data connector.

    Returns a result dict (same shape as ``search_events_on_web``) on
    success, or ``None`` when no connector covers this query.
    """
    result = search_sports_events(subject, start_time, end_time, timezone_str)
    if result is not None:
        return result

    # Future connectors (music, movies, conferences, …) go here.

    return None
