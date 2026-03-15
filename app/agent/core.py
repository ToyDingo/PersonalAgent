from __future__ import annotations

import asyncio
import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from app.google.calendar_service import (
    _normalize_timezone,
    create_or_update_event,
    delete_events,
    list_upcoming_events,
    normalize_event,
    normalize_events,
    resolve_delete_candidates,
    resolve_edit_candidates,
    search_events,
    update_events_by_id,
)
from app.llm.openai_client import OpenAIClient
from app.web.search_service import search_events_on_web


def _derive_action(tool_results: List[Dict[str, Any]]) -> str:
    if not tool_results:
        return "none"
    names = {str(item.get("name")) for item in tool_results}
    has_create = bool(names.intersection({"create_event", "batch_create_events"}))
    has_delete = "delete_calendar_events" in names
    has_edit = "edit_calendar_events" in names
    has_web_lookup = bool(names.intersection({"search_web_for_events", "search_official_sources"}))
    has_retrieve = bool(names.intersection({"get_upcoming_events", "search_calendar_events"}))
    if has_delete and (has_retrieve or has_web_lookup):
        return "mixed"
    if has_edit and (has_retrieve or has_web_lookup):
        return "mixed"
    if has_create:
        return "create"
    if has_edit:
        return "edit"
    if has_delete:
        return "delete"
    if has_retrieve or has_web_lookup:
        return "retrieve"
    return "none"


# Single-user local app: in-memory confirmation state is enough for v1 UI flow.
PENDING_CONFIRMATIONS: Dict[str, Dict[str, Any]] = {}
WEB_SEARCH_OPENAI_API_KEY = ""
WEB_SEARCH_MODEL = "gpt-4o-mini"


def _resolve_web_search_mode(context: Dict[str, Any]) -> str:
    """
    Determine web-search routing mode from explicit user context.
    Returns one of: public, private, auto.
    """
    raw = context.get("event_visibility")
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"public", "private", "auto"}:
            return lowered
    return "auto"


def _iso_at_local_day(d: date, hour: int, minute: int, second: int) -> str:
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    dt = datetime.combine(d, time(hour, minute, second)).replace(tzinfo=local_tz)
    return dt.isoformat()


def _end_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _choose_year_for_month(month_num: int, year_hint: str | None, today: date) -> int:
    if year_hint == "this year":
        return today.year
    if year_hint == "next year":
        return today.year + 1
    if year_hint and year_hint.isdigit():
        return int(year_hint)
    return today.year if month_num >= today.month else today.year + 1


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _event_dedupe_key(event: Dict[str, Any]) -> str:
    return "|".join(
        [
            str(event.get("summary", "")),
            str(event.get("start_iso", "")),
            str(event.get("end_iso", "")),
            str(event.get("html_link", "")),
            str(event.get("source_calendar", "")),
        ]
    )


def _resolve_time_window(user_message: str, now_local: datetime) -> Dict[str, str] | None:
    text = user_message.lower().strip()
    month_map = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    today = now_local.date()
    wd = now_local.weekday()

    local_tz_str = _normalize_timezone(str(now_local.tzinfo or timezone.utc))
    local_tz = now_local.tzinfo or timezone.utc
    has_past_context = bool(
        re.search(
            r"\b(earlier|so far|past|history|historical|previous|already|"
            r"before now|to date|up to now)\b",
            text,
        )
    )

    def _iso_at_local_datetime(dt: datetime) -> str:
        normalized = dt.astimezone(local_tz)
        return normalized.isoformat()

    def window_for_day(target_day: date, phrase: str) -> Dict[str, str]:
        return {
            "source_phrase": phrase,
            "start_iso": _iso_at_local_day(target_day, 0, 0, 0),
            "end_iso": _iso_at_local_day(target_day, 23, 59, 59),
            "timezone": local_tz_str,
        }

    def window_for_range(start_day: date, end_day: date, phrase: str) -> Dict[str, str]:
        return {
            "source_phrase": phrase,
            "start_iso": _iso_at_local_day(start_day, 0, 0, 0),
            "end_iso": _iso_at_local_day(end_day, 23, 59, 59),
            "timezone": local_tz_str,
        }

    def window_for_current_period(start_day: date, end_day: date, phrase: str) -> Dict[str, str]:
        period_start = datetime.combine(start_day, time(0, 0, 0)).replace(tzinfo=local_tz)
        period_end = datetime.combine(end_day, time(23, 59, 59)).replace(tzinfo=local_tz)
        if has_past_context:
            end_dt = now_local if now_local <= period_end else period_end
            return {
                "source_phrase": f"{phrase} (so far)",
                "start_iso": _iso_at_local_datetime(period_start),
                "end_iso": _iso_at_local_datetime(end_dt),
                "timezone": local_tz_str,
            }
        start_dt = now_local if now_local >= period_start else period_start
        return {
            "source_phrase": phrase,
            "start_iso": _iso_at_local_datetime(start_dt),
            "end_iso": _iso_at_local_datetime(period_end),
            "timezone": local_tz_str,
        }

    if "today so far" in text or "so far today" in text:
        start_dt = datetime.combine(today, time(0, 0, 0)).replace(tzinfo=local_tz)
        return {
            "source_phrase": "today so far",
            "start_iso": _iso_at_local_datetime(start_dt),
            "end_iso": _iso_at_local_datetime(now_local),
            "timezone": local_tz_str,
        }

    if "this morning" in text:
        start_dt = datetime.combine(today, time(0, 0, 0)).replace(tzinfo=local_tz)
        noon_dt = datetime.combine(today, time(12, 0, 0)).replace(tzinfo=local_tz)
        end_dt = now_local if now_local <= noon_dt else noon_dt
        return {
            "source_phrase": "this morning",
            "start_iso": _iso_at_local_datetime(start_dt),
            "end_iso": _iso_at_local_datetime(end_dt),
            "timezone": local_tz_str,
        }

    if "this weekend" in text:
        if wd == 6:
            saturday = today - timedelta(days=1)
        else:
            saturday = today + timedelta(days=(5 - wd) % 7)
        sunday = saturday + timedelta(days=1)
        return window_for_current_period(saturday, sunday, "this weekend")

    if "next weekend" in text:
        if wd == 6:
            base_saturday = today - timedelta(days=1)
        else:
            base_saturday = today + timedelta(days=(5 - wd) % 7)
        saturday = base_saturday + timedelta(days=7)
        sunday = saturday + timedelta(days=1)
        return {
            "source_phrase": "next weekend",
            "start_iso": _iso_at_local_day(saturday, 0, 0, 0),
            "end_iso": _iso_at_local_day(sunday, 23, 59, 59),
            "timezone": local_tz_str,
        }

    if "today" in text:
        return window_for_current_period(today, today, "today")

    if "tomorrow" in text:
        return window_for_day(today + timedelta(days=1), "tomorrow")

    iso_date_match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso_date_match:
        year_num = int(iso_date_match.group(1))
        month_num = int(iso_date_match.group(2))
        day_num = int(iso_date_match.group(3))
        try:
            return window_for_day(
                date(year_num, month_num, day_num),
                iso_date_match.group(0),
            )
        except ValueError:
            pass

    slash_date_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if slash_date_match:
        month_num = int(slash_date_match.group(1))
        day_num = int(slash_date_match.group(2))
        year_num = int(slash_date_match.group(3))
        try:
            return window_for_day(
                date(year_num, month_num, day_num),
                slash_date_match.group(0),
            )
        except ValueError:
            pass

    month_day_year_match = re.search(
        (
            r"\b("
            r"january|february|march|april|may|june|july|august|"
            r"september|october|november|december"
            r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(20\d{2})\b"
        ),
        text,
    )
    if month_day_year_match:
        month_name = month_day_year_match.group(1)
        day_num = int(month_day_year_match.group(2))
        year_num = int(month_day_year_match.group(3))
        month_num = month_map[month_name]
        try:
            return window_for_day(
                date(year_num, month_num, day_num),
                month_day_year_match.group(0),
            )
        except ValueError:
            pass

    next_days = re.search(r"\bnext\s+(\d+)\s+days?\b", text)
    if next_days:
        num_days = max(1, int(next_days.group(1)))
        end_day = today + timedelta(days=num_days)
        return {
            "source_phrase": f"next {num_days} days",
            "start_iso": _iso_at_local_datetime(now_local),
            "end_iso": _iso_at_local_day(end_day, 23, 59, 59),
            "timezone": local_tz_str,
        }

    next_weeks = re.search(r"\bnext\s+(\d+)\s+weeks?\b", text)
    if next_weeks:
        num_weeks = max(1, int(next_weeks.group(1)))
        end_day = today + timedelta(days=(num_weeks * 7))
        return {
            "source_phrase": f"next {num_weeks} weeks",
            "start_iso": _iso_at_local_datetime(now_local),
            "end_iso": _iso_at_local_day(end_day, 23, 59, 59),
            "timezone": local_tz_str,
        }

    if "this week" in text:
        start_day = today - timedelta(days=wd)
        end_day = start_day + timedelta(days=6)
        return window_for_current_period(start_day, end_day, "this week")

    if "this month" in text:
        start_day = date(today.year, today.month, 1)
        end_day = _end_of_month(today.year, today.month)
        return window_for_current_period(start_day, end_day, "this month")

    if "this quarter" in text:
        current_quarter = ((today.month - 1) // 3) + 1
        quarter_start_month = ((current_quarter - 1) * 3) + 1
        start_day = date(today.year, quarter_start_month, 1)
        end_day = _end_of_month(today.year, quarter_start_month + 2)
        return window_for_current_period(start_day, end_day, "this quarter")

    in_days = re.search(r"\bin\s+(\d+)\s+days?\b", text)
    if in_days:
        day_offset = int(in_days.group(1))
        return window_for_day(today + timedelta(days=day_offset), f"in {day_offset} days")

    this_weekday = re.search(
        r"\b(this|next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        text,
    )
    if this_weekday:
        qualifier = this_weekday.group(1)
        day_name = this_weekday.group(2)
        target_wd = weekday_map[day_name]
        delta = (target_wd - wd) % 7
        if qualifier == "next":
            delta = 7 if delta == 0 else delta + 7
        target_day = today + timedelta(days=delta)
        return window_for_day(target_day, f"{qualifier} {day_name}")

    quarter_match = re.search(r"\b(?:q([1-4])|quarter\s+([1-4]))(?:\s+(this year|next year|20\d{2}))?\b", text)
    if quarter_match:
        quarter_num = int(quarter_match.group(1) or quarter_match.group(2))
        year_hint = quarter_match.group(3)
        if year_hint == "this year":
            year_num = today.year
        elif year_hint == "next year":
            year_num = today.year + 1
        elif year_hint and year_hint.isdigit():
            year_num = int(year_hint)
        else:
            current_quarter = ((today.month - 1) // 3) + 1
            year_num = today.year if quarter_num >= current_quarter else today.year + 1
        quarter_start_month = ((quarter_num - 1) * 3) + 1
        start_day = date(year_num, quarter_start_month, 1)
        end_day = _end_of_month(year_num, quarter_start_month + 2)
        return window_for_range(start_day, end_day, quarter_match.group(0))

    season_match = re.search(
        r"\b(spring|summer|fall|autumn|winter)\b(?:\s+(this year|next year|20\d{2}))?",
        text,
    )
    if season_match:
        season = season_match.group(1)
        year_hint = season_match.group(2)
        if season in {"fall", "autumn"}:
            season = "autumn"
        if year_hint == "this year":
            base_year = today.year
        elif year_hint == "next year":
            base_year = today.year + 1
        elif year_hint and year_hint.isdigit():
            base_year = int(year_hint)
        else:
            base_year = today.year

        if season == "spring":
            start_day = date(base_year, 3, 1)
            end_day = date(base_year, 5, 31)
            if not year_hint and end_day < today:
                start_day = date(base_year + 1, 3, 1)
                end_day = date(base_year + 1, 5, 31)
        elif season == "summer":
            start_day = date(base_year, 6, 1)
            end_day = date(base_year, 8, 31)
            if not year_hint and end_day < today:
                start_day = date(base_year + 1, 6, 1)
                end_day = date(base_year + 1, 8, 31)
        elif season == "autumn":
            start_day = date(base_year, 9, 1)
            end_day = date(base_year, 11, 30)
            if not year_hint and end_day < today:
                start_day = date(base_year + 1, 9, 1)
                end_day = date(base_year + 1, 11, 30)
        else:
            # Winter spans year boundary: Dec -> Feb.
            start_year = base_year
            if not year_hint and today.month <= 2:
                start_year = today.year - 1
            start_day = date(start_year, 12, 1)
            end_day = _end_of_month(start_year + 1, 2)
            if not year_hint and end_day < today:
                start_day = date(start_year + 1, 12, 1)
                end_day = _end_of_month(start_year + 2, 2)
        return window_for_range(start_day, end_day, season_match.group(0))

    week_of_month_match = re.search(
        (
            r"\b(first|second|third|fourth|last)\s+week\s+of\s+("
            r"january|february|march|april|may|june|july|august|"
            r"september|october|november|december"
            r")\b(?:\s+of)?(?:\s+(this year|next year|20\d{2}))?"
        ),
        text,
    )
    if week_of_month_match:
        ordinal = week_of_month_match.group(1)
        month_name = week_of_month_match.group(2)
        year_hint = week_of_month_match.group(3)
        month_num = month_map[month_name]
        year_num = _choose_year_for_month(month_num, year_hint, today)
        month_start = date(year_num, month_num, 1)
        month_end = _end_of_month(year_num, month_num)

        if ordinal == "last":
            start_day = month_end - timedelta(days=6)
            if start_day < month_start:
                start_day = month_start
            end_day = month_end
        else:
            ordinal_index = {"first": 0, "second": 1, "third": 2, "fourth": 3}[ordinal]
            start_day = month_start + timedelta(days=ordinal_index * 7)
            end_day = start_day + timedelta(days=6)
            if start_day > month_end:
                start_day = month_end
            if end_day > month_end:
                end_day = month_end
        return window_for_range(start_day, end_day, week_of_month_match.group(0))

    month_match = re.search(
        (
            r"\b("
            r"january|february|march|april|may|june|july|august|"
            r"september|october|november|december"
            r")\b(?:\s+of)?(?:\s+(this year|next year|\d{4}))?"
        ),
        text,
    )
    if month_match:
        month_name = month_match.group(1)
        year_hint = month_match.group(2)
        month_num = month_map[month_name]
        year_num = _choose_year_for_month(month_num, year_hint, today)

        start_day = date(year_num, month_num, 1)
        end_day = _end_of_month(year_num, month_num)
        return window_for_range(start_day, end_day, month_match.group(0))

    year_match = re.search(r"(?<![\d/-])(this year|next year|20\d{2})(?![\d/-])", text)
    if year_match:
        year_token = year_match.group(1)
        if year_token == "this year":
            return window_for_current_period(
                date(today.year, 1, 1),
                date(today.year, 12, 31),
                "this year",
            )
        elif year_token == "next year":
            year_num = today.year + 1
        else:
            year_num = int(year_token)
        return window_for_range(
            date(year_num, 1, 1),
            date(year_num, 12, 31),
            year_token,
        )

    return None


@tool
def get_upcoming_events(max_results: int = 200) -> Dict[str, Any]:
    """Get upcoming events from the primary Google Calendar."""
    events = list_upcoming_events(max_results=max_results)
    return {"events": normalize_events(events)}


@tool
def search_calendar_events(
    query: str | None = None,
    max_results: int = 200,
    start_time: str | None = None,
    end_time: str | None = None,
    weekday: str | None = None,
    count: int | None = None,
    allow_past: bool = False,
) -> Dict[str, Any]:
    """
    Search calendar events with granular filters.

    Useful for requests like:
    - next 3 events in a specific series
    - all events across the next 2 Saturdays
    """
    now_utc = datetime.now(timezone.utc)
    if not allow_past:
        if start_time:
            try:
                parsed_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                if parsed_start.tzinfo is None:
                    parsed_start = parsed_start.replace(tzinfo=timezone.utc)
                if parsed_start < now_utc:
                    start_time = now_utc.isoformat()
            except ValueError:
                start_time = now_utc.isoformat()
        if end_time:
            try:
                parsed_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                if parsed_end.tzinfo is None:
                    parsed_end = parsed_end.replace(tzinfo=timezone.utc)
                if parsed_end < now_utc:
                    end_time = None
            except ValueError:
                end_time = None

    events = search_events(
        query=query,
        max_results=max_results,
        start_time=start_time,
        end_time=end_time,
        weekday=weekday,
        count=count,
        allow_past=allow_past,
    )
    return {
        "events": normalize_events(events),
        "effective_start_time": start_time,
        "effective_end_time": end_time,
        "allow_past": allow_past,
    }


def _stage_single_event(
    summary: str,
    start: str,
    end: str,
    timezone: str,
    description: str | None = None,
    event_options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a single staged event candidate (shared by create_event and batch)."""
    iana_tz = _normalize_timezone(timezone)
    event_body: Dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": iana_tz},
        "end": {"dateTime": end, "timeZone": iana_tz},
    }
    if description:
        event_body["description"] = description
    if event_options:
        event_body.update(_sanitize_event_overrides(event_options))
    candidate = normalize_event(event_body)
    if "reminders" in event_body:
        candidate["reminders"] = copy.deepcopy(event_body["reminders"])
    for extra_key in ("visibility", "colorId", "eventType"):
        if extra_key in event_body:
            candidate[extra_key] = event_body[extra_key]
    candidate["id"] = f"candidate-{uuid4()}"
    candidate["status"] = "pending_confirmation"
    candidate["source_calendar"] = "candidate"
    return {
        "requires_confirmation": True,
        "candidate_event": candidate,
        "event_payload": event_body,
    }


@tool
def create_event(
    summary: str,
    start: str,
    end: str,
    timezone: str,
    description: str | None = None,
    event_options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Stage an event candidate for confirmation before creating.

    Optional event_options can include Google Calendar fields such as:
    reminders, location, visibility, colorId, attendees, recurrence,
    conferenceData, attachments, and extendedProperties.
    """
    return _stage_single_event(
        summary,
        start,
        end,
        timezone,
        description,
        event_options,
    )


@tool
def batch_create_events(
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Stage multiple event candidates for confirmation in a single call.

    Each item in *events* must have: summary, start, end, timezone.
    Optional: description plus Google Calendar options either inside
    event_options or as top-level keys (e.g. reminders, attendees, recurrence).

    Prefer this over calling create_event many times when adding more than
    one event (e.g. a full sports schedule).
    """
    staged: List[Dict[str, Any]] = []
    payloads: Dict[str, Dict[str, Any]] = {}
    for item in events:
        start_value = str(item.get("start") or item.get("start_iso") or "")
        end_value = str(item.get("end") or item.get("end_iso") or "")
        result = _stage_single_event(
            summary=str(item.get("summary", "")),
            start=start_value,
            end=end_value,
            timezone=str(item.get("timezone", "UTC")),
            description=item.get("description"),
            event_options=_extract_event_options_from_item(item),
        )
        staged.append(result["candidate_event"])
        cid = result["candidate_event"]["id"]
        payloads[cid] = result["event_payload"]
    return {
        "requires_confirmation": True,
        "candidate_events": staged,
        "event_payloads": payloads,
        "count": len(staged),
    }


@tool
def search_official_sources(
    subject: str,
    start_time: str | None = None,
    end_time: str | None = None,
    timezone: str = "UTC",
) -> Dict[str, Any]:
    """
    Search official data-source APIs (sports leagues, racing calendars, etc.)
    for event schedules.  Use this FIRST for sports, racing, and similar
    event queries before falling back to search_web_for_events.

    Covered leagues: MLB, NFL, NBA, NHL, MLS, WNBA, UFC, PGA, college
    sports (ESPN), plus Formula 1 (Jolpica-F1) and MLB (MLB Stats API
    fallback).

    Returns events in the same format as search_web_for_events.  If the
    domain is not covered, returns source='not_covered'.
    """
    from app.data_sources.router import try_official_source

    result = try_official_source(subject, start_time, end_time, timezone)
    if result is not None:
        return result

    return {
        "query": subject,
        "search_results": [],
        "documents_count": 0,
        "events": [],
        "events_count": 0,
        "source": "not_covered",
        "message": (
            "No official data source covers this query. "
            "Try search_web_for_events instead."
        ),
    }


@tool
def search_web_for_events(
    subject: str,
    timeframe_hint: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_results: int = 200,
    max_events: int = 200,
    include_extra_details: bool = False,
    timezone: str = "UTC",
) -> Dict[str, Any]:
    """
    Search the web for real-world event schedule information and return
    calendar-ready candidates with start/end datetime fields.
    """
    return search_events_on_web(
        openai_api_key=WEB_SEARCH_OPENAI_API_KEY,
        model=WEB_SEARCH_MODEL,
        subject=subject,
        timeframe_hint=timeframe_hint,
        start_time=start_time,
        end_time=end_time,
        max_results=max_results,
        max_events=max_events,
        include_extra_details=include_extra_details,
        timezone=timezone,
    )


@tool
def delete_calendar_events(
    event_ids: List[str] | None = None,
    query: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_results: int = 20,
    delete_series: bool = False,
    allow_past: bool = False,
) -> Dict[str, Any]:
    """
    Delete events by explicit IDs or by search query.

    Supports single-event delete, multi-event delete, and entire recurring
    series delete when delete_series is true.
    """
    resolved = resolve_delete_candidates(
        event_ids=event_ids,
        query=query,
        start_time=start_time,
        end_time=end_time,
        max_results=max_results,
        allow_past=allow_past,
    )
    candidates = normalize_events(resolved.get("candidates", []))
    return {
        "requires_confirmation": True,
        "delete_series": delete_series,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "not_found": resolved.get("not_found", []),
        "not_found_count": int(resolved.get("not_found_count", 0)),
    }


@tool
def edit_calendar_events(
    event_ids: List[str] | None = None,
    query: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_results: int = 20,
    allow_past: bool = False,
    edit_scope: str = "selected",
    summary: str | None = None,
    description: str | None = None,
    location: str | None = None,
    start: str | None = None,
    end: str | None = None,
    timezone: str | None = None,
    event_options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Stage candidate events for confirmation before applying updates.

    Uses multi-strategy matching (exact title, partial, token overlap, fuzzy)
    to find events even when Google's built-in text search misses.

    edit_scope controls what gets updated on confirmation:
      - "selected": update only the selected event instances (default).
      - "series": update the entire recurring series for each selected event.

    Updates can include summary (title), description, location, start, end,
    timezone, and supported Google Calendar fields through event_options
    (e.g. reminders, attendees, recurrence, visibility, colorId).
    """
    update_fields: Dict[str, Any] = {}
    for key, value in {
        "summary": summary,
        "description": description,
        "location": location,
        "start": start,
        "end": end,
        "timezone": timezone,
    }.items():
        if value is not None:
            update_fields[key] = value
    if event_options:
        update_fields.update(_sanitize_event_overrides(event_options))

    if not update_fields:
        return {
            "requires_confirmation": False,
            "error": "no_update_fields",
            "message": "No update fields provided for edit request.",
        }

    if ("start" in update_fields) ^ ("end" in update_fields):
        return {
            "requires_confirmation": False,
            "error": "incomplete_time_update",
            "message": "Both start and end must be provided together for time edits.",
        }

    valid_scopes = {"selected", "series"}
    if edit_scope not in valid_scopes:
        edit_scope = "selected"

    resolved = resolve_edit_candidates(
        event_ids=event_ids,
        query=query,
        start_time=start_time,
        end_time=end_time,
        max_results=max_results,
        allow_past=allow_past,
        include_series=True,
    )
    candidates = normalize_events(resolved.get("candidates", []))
    return {
        "requires_confirmation": True,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "not_found": resolved.get("not_found", []),
        "not_found_count": int(resolved.get("not_found_count", 0)),
        "update_fields": update_fields,
        "edit_scope": edit_scope,
        "series": resolved.get("series", []),
        "series_count": int(resolved.get("series_count", 0)),
        "diagnostics": resolved.get("diagnostics", {}),
    }


def _extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "\n".join(p for p in parts if p).strip()
    return str(content)


def _user_explicitly_requests_past(user_message: str) -> bool:
    text = user_message.lower()
    markers = [
        "past",
        "history",
        "historical",
        "last ",
        "previous",
        "ago",
        "yesterday",
        "earlier",
        "earlier today",
        "back in",
        "this morning",
        "so far",
        "before now",
        "already",
    ]
    return any(marker in text for marker in markers)


def _detect_primary_intent(user_message: str) -> str:
    text = user_message.lower()
    if re.search(r"\b(add|create|insert|put)\b", text):
        return "add"
    if re.search(r"\b(delete|remove|cancel)\b", text):
        return "delete"
    if re.search(r"\b(edit|update|change|reschedule|move|rename)\b", text):
        return "edit"
    has_schedule_term = bool(re.search(r"\bschedule(?:d|ing)?\b", text))
    has_retrieve_markers = bool(
        re.search(
            r"\b(show|list|find|get|what|when|which|who|where|upcoming|"
            r"yesterday|last|previous|past|history|historical|earlier)\b",
            text,
        )
    )
    looks_like_question = (
        "?" in text
        or text.startswith(
            (
                "what",
                "when",
                "which",
                "who",
                "where",
                "did ",
                "do ",
                "can you show",
                "can you list",
            )
        )
    )
    if has_schedule_term and (has_retrieve_markers or looks_like_question):
        return "retrieve"
    if has_schedule_term:
        return "add"
    if re.search(r"\b(show|list|find|get|what|when|upcoming)\b", text):
        return "retrieve"
    return "unknown"


def _extract_named_event_query(user_message: str) -> str | None:
    """
    Extract a compact title query from phrases like:
    - "events named vet"
    - "events called annual checkup"
    """
    text = user_message.lower()
    match = re.search(r"\b(?:named|called)\s+([a-z0-9 _-]{2,80})", text)
    if not match:
        return None
    candidate = match.group(1).strip(" '\"")
    candidate = re.sub(
        r"\s+\b(for|in|on|from|during|this|next|last|past|so)\b.*$",
        "",
        candidate,
    ).strip()
    if len(candidate) < 2:
        return None
    return candidate


def _extract_bulk_rename_request(user_message: str) -> Dict[str, str] | None:
    """
    Extract rename intent from phrases like:
    - "change the name of all events this year name vet robotic to vex robotics"
    - "rename events called old title to new title"
    """
    text = user_message.strip()
    if not re.search(r"\b(change|rename|update)\b", text, flags=re.IGNORECASE):
        return None
    if not re.search(r"\bto\b", text, flags=re.IGNORECASE):
        return None

    rename_match = re.search(
        r"\b(?:named|name|called)\s+(.+?)\s+\bto\b\s+(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if not rename_match:
        return None

    old_name = rename_match.group(1).strip(" '\".,")
    new_name = rename_match.group(2).strip(" '\".,")
    if not old_name or not new_name:
        return None

    return {"old_name": old_name, "new_name": new_name}


def _parse_iso_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _normalize_web_event_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": None,
        "summary": item.get("summary"),
        "description": item.get("description"),
        "location": None,
        "status": "external_candidate",
        "html_link": item.get("source_url"),
        "start_iso": item.get("start"),
        "end_iso": item.get("end"),
        "timezone": item.get("timezone") or "UTC",
        "is_all_day": False,
        "source_calendar": "web",
    }


def _sanitize_reminders(raw_value: Any) -> Dict[str, Any] | None:
    if not isinstance(raw_value, dict):
        return None
    sanitized: Dict[str, Any] = {}
    if "useDefault" in raw_value:
        sanitized["useDefault"] = bool(raw_value.get("useDefault"))
    if "overrides" in raw_value and isinstance(raw_value.get("overrides"), list):
        overrides: List[Dict[str, Any]] = []
        for item in raw_value["overrides"]:
            if not isinstance(item, dict):
                continue
            method = str(item.get("method", "")).strip().lower()
            if method not in {"popup", "email"}:
                continue
            try:
                minutes = int(item.get("minutes"))
            except (TypeError, ValueError):
                continue
            minutes = max(0, min(minutes, 40320))
            overrides.append({"method": method, "minutes": minutes})
        sanitized["overrides"] = overrides
    return sanitized or None


def _sanitize_event_overrides(raw_value: Any) -> Dict[str, Any]:
    if not isinstance(raw_value, dict):
        return {}

    allowed_direct_fields = {
        "location",
        "visibility",
        "transparency",
        "colorId",
        "guestsCanInviteOthers",
        "guestsCanModify",
        "guestsCanSeeOtherGuests",
        "anyoneCanAddSelf",
        "eventType",
    }
    allowed_object_or_list_fields = {
        "recurrence",
        "attendees",
        "conferenceData",
        "attachments",
        "extendedProperties",
        "source",
    }
    sanitized: Dict[str, Any] = {}
    for key in allowed_direct_fields:
        if key in raw_value:
            sanitized[key] = raw_value.get(key)

    reminders = _sanitize_reminders(raw_value.get("reminders"))
    if reminders is not None:
        sanitized["reminders"] = reminders

    for key in allowed_object_or_list_fields:
        value = raw_value.get(key)
        if isinstance(value, (dict, list)):
            sanitized[key] = copy.deepcopy(value)

    return sanitized


def _extract_event_options_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    from_nested = _sanitize_event_overrides(item.get("event_options"))
    from_top_level = _sanitize_event_overrides(item)
    merged = dict(from_nested)
    merged.update(from_top_level)
    return merged


def _merge_event_options(
    default_options: Dict[str, Any] | None,
    explicit_options: Dict[str, Any] | None,
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(default_options, dict):
        merged.update(copy.deepcopy(default_options))
    if isinstance(explicit_options, dict):
        merged.update(copy.deepcopy(explicit_options))
    return merged


def _derive_default_event_options(message: str, context: Dict[str, Any]) -> Dict[str, Any]:
    defaults = _sanitize_event_overrides(context.get("event_defaults"))
    lowered = message.lower()
    if any(
        token in lowered
        for token in ("no reminder", "no reminders", "without reminder", "without reminders")
    ):
        defaults["reminders"] = {"useDefault": False, "overrides": []}
        return defaults

    if not any(token in lowered for token in ("remind", "reminder", "alert", "notification")):
        return defaults

    word_to_num = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    reminder_match = re.search(
        r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(minute|minutes|min|hour|hours|day|days)\s*(?:before|ahead)?\b",
        lowered,
    )
    if reminder_match:
        quantity_raw = reminder_match.group(1)
        try:
            quantity = int(quantity_raw)
        except ValueError:
            quantity = word_to_num.get(quantity_raw, 0)
        unit = reminder_match.group(2)
        multiplier = 1
        if unit in {"hour", "hours"}:
            multiplier = 60
        elif unit in {"day", "days"}:
            multiplier = 1440
        minutes = max(0, min(quantity * multiplier, 40320))
        defaults["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": minutes}],
        }
    return defaults


def _build_external_candidate_description(candidate: Dict[str, Any]) -> str | None:
    """Preserve discovered metadata while keeping source traceability."""
    description = str(candidate.get("description") or "").strip()
    html_link = str(candidate.get("html_link") or "").strip()
    if html_link and html_link not in description:
        if description:
            return f"{description}\nSource: {html_link}"
        return f"Source: {html_link}"
    return description or None


def _clean_search_args(
    raw_args: Dict[str, Any],
    now_utc: datetime,
    explicit_past_requested: bool,
) -> Dict[str, Any]:
    """
    Start from defaults every time, then apply validated user/model values.
    This prevents stale or malformed arguments from leaking across calls.
    """
    cleaned: Dict[str, Any] = {
        "query": None,
        "max_results": 200,
        "start_time": None,
        "end_time": None,
        "weekday": None,
        "count": None,
        "allow_past": False,
    }

    if isinstance(raw_args.get("query"), str):
        cleaned["query"] = raw_args["query"]

    if raw_args.get("max_results") is not None:
        try:
            cleaned["max_results"] = max(1, min(200, int(raw_args["max_results"])))
        except (TypeError, ValueError):
            cleaned["max_results"] = 200

    if isinstance(raw_args.get("start_time"), str):
        cleaned["start_time"] = raw_args["start_time"]
    if isinstance(raw_args.get("end_time"), str):
        cleaned["end_time"] = raw_args["end_time"]
    if isinstance(raw_args.get("weekday"), str):
        cleaned["weekday"] = raw_args["weekday"]

    if raw_args.get("count") is not None:
        try:
            cleaned["count"] = max(1, min(52, int(raw_args["count"])))
        except (TypeError, ValueError):
            cleaned["count"] = None

    allow_past = bool(raw_args.get("allow_past", False)) and explicit_past_requested
    cleaned["allow_past"] = allow_past

    if not allow_past:
        start_dt = _parse_iso_or_none(cleaned["start_time"])
        end_dt = _parse_iso_or_none(cleaned["end_time"])
        if start_dt is None or start_dt < now_utc:
            cleaned["start_time"] = now_utc.isoformat()
        if end_dt is not None and end_dt < now_utc:
            cleaned["end_time"] = None

    return cleaned


def _clean_tool_args(
    tool_name: str,
    raw_args: Dict[str, Any],
    now_utc: datetime,
    explicit_past_requested: bool,
    resolved_time_window: Dict[str, str] | None,
    default_event_options: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if tool_name == "get_upcoming_events":
        max_results = 200
        if raw_args.get("max_results") is not None:
            try:
                max_results = max(1, min(200, int(raw_args["max_results"])))
            except (TypeError, ValueError):
                max_results = 200
        return {"max_results": max_results}

    if tool_name == "search_calendar_events":
        cleaned_search = _clean_search_args(
            raw_args=raw_args,
            now_utc=now_utc,
            explicit_past_requested=explicit_past_requested,
        )
        raw_start_provided = isinstance(raw_args.get("start_time"), str) and bool(
            raw_args.get("start_time", "").strip()
        )
        raw_end_provided = isinstance(raw_args.get("end_time"), str) and bool(
            raw_args.get("end_time", "").strip()
        )
        if resolved_time_window:
            if not raw_start_provided:
                cleaned_search["start_time"] = resolved_time_window.get("start_iso")
            if not raw_end_provided:
                cleaned_search["end_time"] = resolved_time_window.get("end_iso")
            window_start = _parse_iso_or_none(cleaned_search.get("start_time"))
            window_end = _parse_iso_or_none(cleaned_search.get("end_time"))
            if explicit_past_requested and (
                (window_start is not None and window_start < now_utc)
                or (window_end is not None and window_end <= now_utc)
            ):
                cleaned_search["allow_past"] = True
        if not cleaned_search.get("allow_past"):
            start_dt = _parse_iso_or_none(cleaned_search.get("start_time"))
            end_dt = _parse_iso_or_none(cleaned_search.get("end_time"))
            if start_dt is None or start_dt < now_utc:
                cleaned_search["start_time"] = now_utc.isoformat()
            if end_dt is not None and end_dt < now_utc:
                cleaned_search["end_time"] = None
        return cleaned_search

    if tool_name == "create_event":
        start_value = raw_args.get("start")
        if start_value is None:
            start_value = raw_args.get("start_iso")
        end_value = raw_args.get("end")
        if end_value is None:
            end_value = raw_args.get("end_iso")
        cleaned: Dict[str, Any] = {
            "summary": str(raw_args.get("summary", "")),
            "start": str(start_value or ""),
            "end": str(end_value or ""),
            "timezone": str(raw_args.get("timezone", "UTC")),
        }
        if raw_args.get("description") is not None:
            cleaned["description"] = str(raw_args["description"])
        explicit_event_options = _extract_event_options_from_item(raw_args)
        merged_event_options = _merge_event_options(
            default_event_options,
            explicit_event_options,
        )
        if merged_event_options:
            cleaned["event_options"] = merged_event_options
        return cleaned

    if tool_name == "batch_create_events":
        raw_events = raw_args.get("events", [])
        if not isinstance(raw_events, list):
            raw_events = []
        sanitized: List[Dict[str, Any]] = []
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            start_value = item.get("start")
            if start_value is None:
                start_value = item.get("start_iso")
            end_value = item.get("end")
            if end_value is None:
                end_value = item.get("end_iso")
            entry: Dict[str, Any] = {
                "summary": str(item.get("summary", "")),
                "start": str(start_value or ""),
                "end": str(end_value or ""),
                "timezone": str(item.get("timezone", "UTC")),
            }
            if item.get("description") is not None:
                entry["description"] = str(item["description"])
            explicit_event_options = _extract_event_options_from_item(item)
            merged_event_options = _merge_event_options(
                default_event_options,
                explicit_event_options,
            )
            if merged_event_options:
                entry["event_options"] = merged_event_options
            sanitized.append(entry)
        return {"events": sanitized}

    if tool_name == "search_official_sources":
        cleaned_official: Dict[str, Any] = {
            "subject": str(raw_args.get("subject", "")).strip(),
            "start_time": None,
            "end_time": None,
            "timezone": "UTC",
        }
        if isinstance(raw_args.get("start_time"), str) and raw_args["start_time"].strip():
            cleaned_official["start_time"] = raw_args["start_time"].strip()
        if isinstance(raw_args.get("end_time"), str) and raw_args["end_time"].strip():
            cleaned_official["end_time"] = raw_args["end_time"].strip()
        if isinstance(raw_args.get("timezone"), str) and raw_args["timezone"].strip():
            cleaned_official["timezone"] = raw_args["timezone"].strip()
        if resolved_time_window:
            if cleaned_official.get("start_time") is None:
                cleaned_official["start_time"] = resolved_time_window.get("start_iso")
            if cleaned_official.get("end_time") is None:
                cleaned_official["end_time"] = resolved_time_window.get("end_iso")
            if cleaned_official.get("timezone") in {"", "UTC"}:
                cleaned_official["timezone"] = resolved_time_window.get("timezone", "UTC")
        return cleaned_official

    if tool_name == "search_web_for_events":
        cleaned_web: Dict[str, Any] = {
            "subject": str(raw_args.get("subject", "")).strip(),
            "timeframe_hint": None,
            "start_time": None,
            "end_time": None,
            "max_results": 200,
            "max_events": 200,
            "include_extra_details": False,
            "timezone": "UTC",
        }
        if isinstance(raw_args.get("timeframe_hint"), str):
            cleaned_web["timeframe_hint"] = raw_args["timeframe_hint"]
        if isinstance(raw_args.get("start_time"), str):
            cleaned_web["start_time"] = raw_args["start_time"]
        if isinstance(raw_args.get("end_time"), str):
            cleaned_web["end_time"] = raw_args["end_time"]
        if raw_args.get("max_results") is not None:
            try:
                cleaned_web["max_results"] = max(1, min(200, int(raw_args["max_results"])))
            except (TypeError, ValueError):
                cleaned_web["max_results"] = 200
        if raw_args.get("max_events") is not None:
            try:
                cleaned_web["max_events"] = max(1, min(200, int(raw_args["max_events"])))
            except (TypeError, ValueError):
                cleaned_web["max_events"] = 200
        cleaned_web["include_extra_details"] = bool(
            raw_args.get("include_extra_details", False)
        )
        if isinstance(raw_args.get("timezone"), str) and raw_args.get("timezone"):
            cleaned_web["timezone"] = str(raw_args["timezone"])
        if resolved_time_window:
            if cleaned_web.get("timeframe_hint") is None:
                cleaned_web["timeframe_hint"] = resolved_time_window.get("source_phrase")
            if cleaned_web.get("start_time") is None:
                cleaned_web["start_time"] = resolved_time_window.get("start_iso")
            if cleaned_web.get("end_time") is None:
                cleaned_web["end_time"] = resolved_time_window.get("end_iso")
            if cleaned_web.get("timezone") in {"", "UTC"}:
                cleaned_web["timezone"] = resolved_time_window.get("timezone", "UTC")
        return cleaned_web

    if tool_name == "delete_calendar_events":
        cleaned_delete: Dict[str, Any] = {
            "event_ids": None,
            "query": None,
            "start_time": None,
            "end_time": None,
            "max_results": 20,
            "delete_series": False,
            "allow_past": False,
        }
        if isinstance(raw_args.get("event_ids"), list):
            cleaned_delete["event_ids"] = [
                str(item) for item in raw_args["event_ids"] if str(item).strip()
            ]
        if isinstance(raw_args.get("query"), str):
            cleaned_delete["query"] = raw_args["query"]
        if isinstance(raw_args.get("start_time"), str):
            cleaned_delete["start_time"] = raw_args["start_time"]
        if isinstance(raw_args.get("end_time"), str):
            cleaned_delete["end_time"] = raw_args["end_time"]
        if raw_args.get("max_results") is not None:
            try:
                cleaned_delete["max_results"] = max(
                    1, min(100, int(raw_args["max_results"]))
                )
            except (TypeError, ValueError):
                cleaned_delete["max_results"] = 20
        cleaned_delete["delete_series"] = bool(raw_args.get("delete_series", False))
        cleaned_delete["allow_past"] = (
            bool(raw_args.get("allow_past", False)) and explicit_past_requested
        )
        if resolved_time_window:
            if cleaned_delete.get("start_time") is None:
                cleaned_delete["start_time"] = resolved_time_window.get("start_iso")
            if cleaned_delete.get("end_time") is None:
                cleaned_delete["end_time"] = resolved_time_window.get("end_iso")
        return cleaned_delete

    if tool_name == "edit_calendar_events":
        cleaned_edit: Dict[str, Any] = {
            "event_ids": None,
            "query": None,
            "start_time": None,
            "end_time": None,
            "max_results": 20,
            "allow_past": False,
            "edit_scope": "selected",
            "summary": None,
            "description": None,
            "location": None,
            "start": None,
            "end": None,
            "timezone": None,
            "event_options": None,
        }
        if isinstance(raw_args.get("event_ids"), list):
            cleaned_edit["event_ids"] = [
                str(item) for item in raw_args["event_ids"] if str(item).strip()
            ]
        if isinstance(raw_args.get("query"), str):
            cleaned_edit["query"] = raw_args["query"]
        if isinstance(raw_args.get("start_time"), str):
            cleaned_edit["start_time"] = raw_args["start_time"]
        if isinstance(raw_args.get("end_time"), str):
            cleaned_edit["end_time"] = raw_args["end_time"]
        if raw_args.get("max_results") is not None:
            try:
                cleaned_edit["max_results"] = max(1, min(100, int(raw_args["max_results"])))
            except (TypeError, ValueError):
                cleaned_edit["max_results"] = 20
        cleaned_edit["allow_past"] = (
            bool(raw_args.get("allow_past", False)) and explicit_past_requested
        )
        scope_raw = raw_args.get("edit_scope", "selected")
        cleaned_edit["edit_scope"] = (
            str(scope_raw) if str(scope_raw) in {"selected", "series"} else "selected"
        )
        for field in ("summary", "description", "location", "start", "end", "timezone"):
            if raw_args.get(field) is not None:
                cleaned_edit[field] = str(raw_args.get(field))
        explicit_event_options = _extract_event_options_from_item(raw_args)
        if explicit_event_options:
            cleaned_edit["event_options"] = explicit_event_options
        if resolved_time_window:
            if cleaned_edit.get("start_time") is None:
                cleaned_edit["start_time"] = resolved_time_window.get("start_iso")
            if cleaned_edit.get("end_time") is None:
                cleaned_edit["end_time"] = resolved_time_window.get("end_iso")
        return cleaned_edit

    return {}


def _handle_operation_confirmation_context(
    context: Dict[str, Any],
    runtime_context: Dict[str, Any],
) -> Dict[str, Any] | None:
    payload = context.get("operation_confirmation") or context.get("delete_confirmation")
    if not isinstance(payload, dict):
        return None

    confirmation_id = str(payload.get("confirmation_id", ""))
    action = str(payload.get("action", "")).lower()
    if not confirmation_id:
        return {
            "result_type": "calendar_events",
            "action": "none",
            "summary": {
                "calendar_id": runtime_context["default_calendar_id"],
                "error": "missing_confirmation_id",
            },
            "events": [],
            "meta": {
                "default_calendar_id": runtime_context["default_calendar_id"],
                "current_datetime_utc": runtime_context["current_datetime_utc"],
                "current_datetime_local": runtime_context["current_datetime_local"],
                "query": "operation_confirmation",
            },
            "tool_results": [],
        }

    pending = PENDING_CONFIRMATIONS.get(confirmation_id)
    if pending is None:
        return {
            "result_type": "calendar_events",
            "action": "none",
            "summary": {
                "calendar_id": runtime_context["default_calendar_id"],
                "error": "confirmation_not_found",
                "confirmation_id": confirmation_id,
            },
            "events": [],
            "meta": {
                "default_calendar_id": runtime_context["default_calendar_id"],
                "current_datetime_utc": runtime_context["current_datetime_utc"],
                "current_datetime_local": runtime_context["current_datetime_local"],
                "query": "operation_confirmation",
            },
            "tool_results": [],
        }

    operation = str(pending.get("operation", ""))
    if action == "cancel":
        PENDING_CONFIRMATIONS.pop(confirmation_id, None)
        cancelled_action_map = {
            "add": "add_cancelled",
            "delete": "delete_cancelled",
            "edit": "edit_cancelled",
        }
        cancelled_action = cancelled_action_map.get(operation, "none")
        return {
            "result_type": "calendar_events",
            "action": cancelled_action,
            "summary": {
                "calendar_id": runtime_context["default_calendar_id"],
                "cancelled": True,
                "operation": operation,
                "confirmation_id": confirmation_id,
            },
            "events": [],
            "meta": {
                "default_calendar_id": runtime_context["default_calendar_id"],
                "current_datetime_utc": runtime_context["current_datetime_utc"],
                "current_datetime_local": runtime_context["current_datetime_local"],
                "query": "operation_confirmation",
            },
            "tool_results": [],
        }

    if action != "confirm":
        return None

    requested_ids = payload.get("selected_event_ids") or payload.get("selected_candidate_ids")
    if not isinstance(requested_ids, list):
        requested_ids = []
    selected_ids = [str(item) for item in requested_ids if str(item).strip()]

    if operation == "delete":
        if not selected_ids:
            selected_ids = [
                str(event.get("id"))
                for event in pending.get("candidates", [])
                if isinstance(event, dict) and event.get("id")
            ]
        delete_result = delete_events(
            event_ids=selected_ids,
            delete_series=bool(pending.get("delete_series", False)),
            allow_past=True,
        )
        PENDING_CONFIRMATIONS.pop(confirmation_id, None)
        return {
            "result_type": "calendar_events",
            "action": "delete",
            "summary": {
                "calendar_id": runtime_context["default_calendar_id"],
                "deleted_count": int(delete_result.get("deleted_count", 0)),
                "deleted_events": delete_result.get("deleted_events", []),
                "not_found_count": int(delete_result.get("not_found_count", 0)),
                "not_found": delete_result.get("not_found", []),
                "errors": delete_result.get("errors", []),
                "confirmation_id": confirmation_id,
            },
            "events": [],
            "meta": {
                "default_calendar_id": runtime_context["default_calendar_id"],
                "current_datetime_utc": runtime_context["current_datetime_utc"],
                "current_datetime_local": runtime_context["current_datetime_local"],
                "query": "operation_confirmation",
            },
            "tool_results": [],
        }

    if operation == "edit":
        if not selected_ids:
            selected_ids = [
                str(event.get("id"))
                for event in pending.get("candidates", [])
                if isinstance(event, dict) and event.get("id")
            ]
        update_fields = pending.get("update_fields", {})
        if not isinstance(update_fields, dict):
            update_fields = {}
        edit_scope = str(pending.get("edit_scope", "selected"))
        update_result = update_events_by_id(
            event_ids=selected_ids,
            update_fields=update_fields,
            update_series=(edit_scope == "series"),
        )
        PENDING_CONFIRMATIONS.pop(confirmation_id, None)
        return {
            "result_type": "calendar_events",
            "action": "edit",
            "summary": {
                "calendar_id": runtime_context["default_calendar_id"],
                "updated_count": int(update_result.get("updated_count", 0)),
                "updated_events": update_result.get("updated_events", []),
                "not_found_count": int(update_result.get("not_found_count", 0)),
                "not_found": update_result.get("not_found", []),
                "errors": update_result.get("errors", []),
                "confirmation_id": confirmation_id,
                "applied_update_fields": update_fields,
                "edit_scope": edit_scope,
            },
            "events": update_result.get("updated_events", []),
            "meta": {
                "default_calendar_id": runtime_context["default_calendar_id"],
                "current_datetime_utc": runtime_context["current_datetime_utc"],
                "current_datetime_local": runtime_context["current_datetime_local"],
                "query": "operation_confirmation",
            },
            "tool_results": [],
        }

    # operation == "add"
    candidates = pending.get("candidates", [])
    payload_by_id = pending.get("payload_by_candidate_id", {})
    max_add_dt = _parse_iso_datetime(runtime_context.get("max_add_event_datetime_local"))
    if not selected_ids:
        selected_ids = [
            str(item.get("id"))
            for item in candidates
            if isinstance(item, dict) and item.get("id")
        ]
    created_events: List[Dict[str, Any]] = []
    not_allowed: List[Dict[str, Any]] = []
    create_errors: List[Dict[str, Any]] = []
    for candidate_id in selected_ids:
        event_payload = payload_by_id.get(candidate_id)
        if not isinstance(event_payload, dict):
            continue
        start_value = (
            event_payload.get("start", {}).get("dateTime")
            if isinstance(event_payload.get("start"), dict)
            else None
        )
        start_dt = _parse_iso_datetime(start_value)
        if max_add_dt is not None and start_dt is not None and start_dt > max_add_dt:
            not_allowed.append(
                {
                    "candidate_id": candidate_id,
                    "start": start_value,
                    "error": "outside_one_year_window",
                }
            )
            continue
        try:
            created = create_or_update_event(event_payload)
            created_events.append(normalize_event(created))
        except Exception as exc:
            create_errors.append(
                {
                    "candidate_id": candidate_id,
                    "error": "create_failed",
                    "message": str(exc),
                }
            )

    PENDING_CONFIRMATIONS.pop(confirmation_id, None)
    return {
        "result_type": "calendar_events",
        "action": "create",
        "summary": {
            "calendar_id": runtime_context["default_calendar_id"],
            "events_created_count": len(created_events),
            "events_added": created_events,
            "not_allowed_count": len(not_allowed),
            "not_allowed": not_allowed,
            "errors": create_errors,
            "confirmation_id": confirmation_id,
        },
        "events": created_events,
        "meta": {
            "default_calendar_id": runtime_context["default_calendar_id"],
            "current_datetime_utc": runtime_context["current_datetime_utc"],
            "current_datetime_local": runtime_context["current_datetime_local"],
            "query": "operation_confirmation",
        },
        "tool_results": [],
    }


async def run_agent_chat(
    llm_client: OpenAIClient,
    message: str,
    context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    LangChain-based tool-enabled interaction with the model.
    """
    context = context or {}
    global WEB_SEARCH_OPENAI_API_KEY, WEB_SEARCH_MODEL
    WEB_SEARCH_OPENAI_API_KEY = llm_client.api_key
    WEB_SEARCH_MODEL = llm_client.model

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()
    max_add_event_datetime_local = now_local + timedelta(days=365)
    runtime_context = {
        "current_datetime_utc": now_utc.isoformat(),
        "current_datetime_local": now_local.isoformat(),
        "max_add_event_datetime_local": max_add_event_datetime_local.isoformat(),
        "default_calendar_id": "primary",
        "explicit_past_requested": _user_explicitly_requests_past(message),
    }

    confirmation_result = _handle_operation_confirmation_context(
        context=context,
        runtime_context=runtime_context,
    )
    if confirmation_result is not None:
        return confirmation_result

    web_search_mode = _resolve_web_search_mode(context)
    runtime_context["web_search_mode"] = web_search_mode
    default_event_options = _derive_default_event_options(message, context)
    runtime_context["default_event_options"] = default_event_options
    resolved_time_window = _resolve_time_window(message, now_local)
    add_window_exceeds_one_year = False
    primary_intent = _detect_primary_intent(message)
    if resolved_time_window:
        resolved_start_dt = _parse_iso_datetime(resolved_time_window.get("start_iso"))
        if resolved_start_dt and resolved_start_dt > max_add_event_datetime_local:
            add_window_exceeds_one_year = True
    if resolved_time_window:
        runtime_context["resolved_time_window"] = resolved_time_window
    runtime_context["add_window_exceeds_one_year"] = add_window_exceeds_one_year
    merged_context = {**runtime_context, **context}
    named_query = _extract_named_event_query(message)
    rename_request = _extract_bulk_rename_request(message)

    def _stage_direct_rename_edit(
        old_name: str,
        new_name: str,
        *,
        fallback_reason: str | None = None,
    ) -> Dict[str, Any]:
        force_allow_past = bool(re.search(r"\ball\b", message.lower()))
        raw_edit_args: Dict[str, Any] = {
            "query": old_name,
            "summary": new_name,
            "max_results": 200,
            "edit_scope": "selected",
            "allow_past": force_allow_past
            or bool(runtime_context.get("explicit_past_requested", False)),
        }
        if resolved_time_window:
            raw_edit_args["start_time"] = resolved_time_window.get("start_iso")
            raw_edit_args["end_time"] = resolved_time_window.get("end_iso")
        cleaned_edit_args = _clean_tool_args(
            "edit_calendar_events",
            raw_edit_args,
            now_utc=now_utc,
            explicit_past_requested=bool(runtime_context.get("explicit_past_requested", False)),
            resolved_time_window=resolved_time_window,
            default_event_options=default_event_options,
        )
        edit_result = edit_calendar_events.invoke(cleaned_edit_args)
        edit_candidates = (
            edit_result.get("candidates", [])
            if isinstance(edit_result, dict) and isinstance(edit_result.get("candidates"), list)
            else []
        )

        if edit_candidates:
            confirmation_id = str(uuid4())
            PENDING_CONFIRMATIONS[confirmation_id] = {
                "operation": "edit",
                "candidates": edit_candidates,
                "update_fields": (edit_result or {}).get("update_fields", {"summary": new_name}),
                "edit_scope": str((edit_result or {}).get("edit_scope", "selected")),
                "created_at_utc": runtime_context["current_datetime_utc"],
            }
            summary: Dict[str, Any] = {
                "calendar_id": runtime_context["default_calendar_id"],
                "operation": "edit",
                "confirmation_id": confirmation_id,
                "candidate_count": len(edit_candidates),
                "candidates": edit_candidates,
                "update_fields": (edit_result or {}).get("update_fields", {"summary": new_name}),
                "edit_scope": str((edit_result or {}).get("edit_scope", "selected")),
                "series": (edit_result or {}).get("series", []),
                "series_count": int((edit_result or {}).get("series_count", 0)),
                "not_found_count": int((edit_result or {}).get("not_found_count", 0)),
                "not_found": (edit_result or {}).get("not_found", []),
                "diagnostics": (edit_result or {}).get("diagnostics", {}),
            }
            if fallback_reason:
                summary["fallback_reason"] = fallback_reason
            return {
                "result_type": "calendar_events",
                "action": "edit_pending_confirmation",
                "summary": summary,
                "events": edit_candidates,
                "meta": {
                    "default_calendar_id": runtime_context["default_calendar_id"],
                    "current_datetime_utc": runtime_context["current_datetime_utc"],
                    "current_datetime_local": runtime_context["current_datetime_local"],
                    "query": message,
                },
                "tool_results": [
                    {
                        "name": "edit_calendar_events",
                        "args": cleaned_edit_args,
                        "result": edit_result,
                    }
                ],
            }

        summary = {
            "calendar_id": runtime_context["default_calendar_id"],
            "events_count": 0,
            "events": [],
            "error": "no_editable_events_found",
            "message": "No matching events were found to edit for this request.",
        }
        if isinstance(edit_result, dict) and edit_result.get("error"):
            summary["edit_error"] = edit_result.get("error")
            summary["edit_message"] = edit_result.get("message")
        if fallback_reason:
            summary["fallback_reason"] = fallback_reason
        return {
            "result_type": "calendar_events",
            "action": "none",
            "summary": summary,
            "events": [],
            "meta": {
                "default_calendar_id": runtime_context["default_calendar_id"],
                "current_datetime_utc": runtime_context["current_datetime_utc"],
                "current_datetime_local": runtime_context["current_datetime_local"],
                "query": message,
            },
            "tool_results": [
                {
                    "name": "edit_calendar_events",
                    "args": cleaned_edit_args,
                    "result": edit_result,
                }
            ],
        }

    # Fast path for simple retrieval prompts so calendar lookups do not depend on
    # an LLM round-trip (avoids user-facing timeouts when model connectivity is flaky).
    if (
        primary_intent == "retrieve"
        and named_query
        and web_search_mode == "private"
        and not re.search(r"\b(add|create|delete|remove|edit|update)\b", message.lower())
    ):
        raw_search_args: Dict[str, Any] = {
            "query": named_query,
            "max_results": 200,
            "allow_past": bool(runtime_context.get("explicit_past_requested", False)),
        }
        if resolved_time_window:
            raw_search_args["start_time"] = resolved_time_window.get("start_iso")
            raw_search_args["end_time"] = resolved_time_window.get("end_iso")
        cleaned_search_args = _clean_tool_args(
            "search_calendar_events",
            raw_search_args,
            now_utc=now_utc,
            explicit_past_requested=bool(runtime_context.get("explicit_past_requested", False)),
            resolved_time_window=resolved_time_window,
            default_event_options=default_event_options,
        )
        fast_events = search_events(
            query=cleaned_search_args.get("query"),
            max_results=int(cleaned_search_args.get("max_results", 200)),
            start_time=cleaned_search_args.get("start_time"),
            end_time=cleaned_search_args.get("end_time"),
            weekday=cleaned_search_args.get("weekday"),
            count=cleaned_search_args.get("count"),
            allow_past=bool(cleaned_search_args.get("allow_past", False)),
        )
        fast_result = {
            "events": normalize_events(fast_events),
            "effective_start_time": cleaned_search_args.get("start_time"),
            "effective_end_time": cleaned_search_args.get("end_time"),
            "allow_past": bool(cleaned_search_args.get("allow_past", False)),
        }
        return {
            "result_type": "calendar_events",
            "action": "retrieve",
            "summary": {
                "calendar_id": runtime_context["default_calendar_id"],
                "events_found_count": len(fast_result.get("events", [])),
                "query": named_query,
            },
            "events": fast_result.get("events", []),
            "meta": {
                "default_calendar_id": runtime_context["default_calendar_id"],
                "current_datetime_utc": runtime_context["current_datetime_utc"],
                "current_datetime_local": runtime_context["current_datetime_local"],
                "query": message,
            },
            "tool_results": [
                {
                    "name": "search_calendar_events",
                    "args": cleaned_search_args,
                    "result": fast_result,
                }
            ],
        }

    if primary_intent == "edit" and rename_request and web_search_mode == "private":
        return _stage_direct_rename_edit(
            old_name=rename_request["old_name"],
            new_name=rename_request["new_name"],
        )

    tools = [
        get_upcoming_events,
        search_calendar_events,
        search_official_sources,
        create_event,
        batch_create_events,
        delete_calendar_events,
        edit_calendar_events,
    ]
    if web_search_mode != "private":
        tools.insert(3, search_web_for_events)
    tool_map = {tool_.name: tool_ for tool_ in tools}

    model = ChatOpenAI(
        api_key=llm_client.api_key,
        model=llm_client.model,
        temperature=0,
    )
    model_with_tools = model.bind_tools(tools)
    model_timeout_seconds = 30.0

    async def _ainvoke_model(call_messages: List[Any]) -> Any:
        return await asyncio.wait_for(
            model_with_tools.ainvoke(call_messages),
            timeout=model_timeout_seconds,
        )

    # Policy-critical prompt: changes here directly affect routing, safety,
    # and data accuracy for all calendar operations.
    system_prompt = """You are a precision calendar operations agent.

Primary objective:
- Return accurate, schema-valid calendar results.
- Minimize latency and unnecessary tool calls.
- Never fabricate events, times, URLs, or outcomes.

Hard routing rules (must follow):
1) If context contains operation_confirmation/delete_confirmation:
   - Do not call search tools.
   - Do not call retrieval tools unless explicitly required for the confirmation operation.
   - Execute only the pending confirmation path and return.
2) For add/delete/edit requests targeting existing calendar items:
   - Prefer calendar tools only.
   - Do not call internet search unless the user asks for external real-world events.
3) For external schedules (sports/concerts/public events):
   - Call search_official_sources first.
   - Call search_web_for_events only if official source returns zero events, not_covered, or explicit provider failure.
   - Never run official and web fallback in the same tool batch.
4) For multiple adds:
   - Use batch_create_events once with all candidates.
5) For writes:
   - Stage first, require explicit confirmation, then execute.
6) For reminders/alerts:
   - Map user intent to explicit event_options.reminders.
   - Preserve reminder settings through staging and final creation.

Internet research quality rules:
7) If internet data is used:
   - Prefer official/authoritative sources.
   - Include source URL in event description when relevant.
   - If sources conflict, choose the most authoritative and note uncertainty.
8) Never invent missing fields.
   - If date/time cannot be verified, omit event and report why.
9) Respect requested timeframe and timezone strictly.

Calendar accuracy rules:
10) Never infer event IDs.
11) Never delete/edit without confirmed target candidates.
12) Never change events outside user-selected IDs in confirmation.
13) Preserve user-requested fields (reminders, location, recurrence, attendees, visibility) when supplied.

Response contract:
14) Respond in strict JSON only.
15) action must match operation outcome:
   - add_pending_confirmation, create, delete_pending_confirmation, delete, edit_pending_confirmation, edit, retrieve, none.
16) Include structured errors instead of broad fallbacks.
17) If confidence is low or ambiguous, ask one concise clarification question (unless confirmation context is active).

Performance rules:
18) Minimize tool calls and duplicate requests.
19) Reuse prior tool results within the same turn.
20) Avoid web search for confirmation/cancel paths and simple calendar-only operations."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                f"User message: {message}\n"
                f"Context JSON: {json.dumps(merged_context)}"
            )
        ),
    ]

    tool_results: List[Dict[str, Any]] = []
    tool_result_cache: Dict[str, Dict[str, Any]] = {}
    web_search_unavailable_in_turn = False
    try:
        response = await _ainvoke_model(messages)
    except Exception as exc:
        if primary_intent == "edit" and rename_request:
            return _stage_direct_rename_edit(
                old_name=rename_request["old_name"],
                new_name=rename_request["new_name"],
                fallback_reason=f"llm_unavailable:{type(exc).__name__}",
            )
        if primary_intent == "retrieve":
            fallback_query = named_query
            fallback_args: Dict[str, Any] = {
                "query": fallback_query,
                "max_results": 200,
                "allow_past": bool(runtime_context.get("explicit_past_requested", False)),
            }
            if resolved_time_window:
                fallback_args["start_time"] = resolved_time_window.get("start_iso")
                fallback_args["end_time"] = resolved_time_window.get("end_iso")
            cleaned_fallback_args = _clean_tool_args(
                "search_calendar_events",
                fallback_args,
                now_utc=now_utc,
                explicit_past_requested=bool(runtime_context.get("explicit_past_requested", False)),
                resolved_time_window=resolved_time_window,
                default_event_options=default_event_options,
            )
            fallback_events = search_events(
                query=cleaned_fallback_args.get("query"),
                max_results=int(cleaned_fallback_args.get("max_results", 200)),
                start_time=cleaned_fallback_args.get("start_time"),
                end_time=cleaned_fallback_args.get("end_time"),
                weekday=cleaned_fallback_args.get("weekday"),
                count=cleaned_fallback_args.get("count"),
                allow_past=bool(cleaned_fallback_args.get("allow_past", False)),
            )
            fallback_result = {
                "events": normalize_events(fallback_events),
                "effective_start_time": cleaned_fallback_args.get("start_time"),
                "effective_end_time": cleaned_fallback_args.get("end_time"),
                "allow_past": bool(cleaned_fallback_args.get("allow_past", False)),
            }
            return {
                "result_type": "calendar_events",
                "action": "retrieve",
                "summary": {
                    "calendar_id": runtime_context["default_calendar_id"],
                    "events_found_count": len(fallback_result.get("events", [])),
                    "fallback_reason": "llm_unavailable",
                    "warning": f"Recovered with direct calendar search after model error: {type(exc).__name__}",
                },
                "events": fallback_result.get("events", []),
                "meta": {
                    "default_calendar_id": runtime_context["default_calendar_id"],
                    "current_datetime_utc": runtime_context["current_datetime_utc"],
                    "current_datetime_local": runtime_context["current_datetime_local"],
                    "query": message,
                },
                "tool_results": [
                    {
                        "name": "search_calendar_events",
                        "args": cleaned_fallback_args,
                        "result": fallback_result,
                    }
                ],
            }
        raise

    def _invoke_tool_safe(tname: str, targs: Dict[str, Any]) -> Dict[str, Any]:
        target_tool = tool_map[tname]
        try:
            return target_tool.invoke(targs)
        except Exception as exc:
            if tname == "search_web_for_events":
                return {
                    "query": "",
                    "search_results": [],
                    "documents_count": 0,
                    "events": [],
                    "events_count": 0,
                    "error": "web_search_unavailable",
                    "message": "Web search request timed out or failed before extraction completed.",
                    "exception": str(exc),
                }
            return {
                "error": "tool_execution_failed",
                "tool_name": tname,
                "message": str(exc),
            }

    for _ in range(4):
        if not getattr(response, "tool_calls", None):
            break

        messages.append(response)
        called_tool_names = {str(call.get("name")) for call in response.tool_calls}
        defer_web_fallback_in_batch = (
            "search_official_sources" in called_tool_names
            and "search_web_for_events" in called_tool_names
        )

        ordered_slots: List[Dict[str, Any]] = []
        futures_map: Dict[Any, int] = {}

        with ThreadPoolExecutor(max_workers=min(len(response.tool_calls), 6)) as pool:
            for idx, call in enumerate(response.tool_calls):
                tool_name = call["name"]
                raw_args = call.get("args", {})
                args = _clean_tool_args(
                    tool_name=tool_name,
                    raw_args=raw_args,
                    now_utc=now_utc,
                    explicit_past_requested=runtime_context["explicit_past_requested"],
                    resolved_time_window=resolved_time_window,
                    default_event_options=default_event_options,
                )
                slot: Dict[str, Any] = {
                    "id": call["id"],
                    "name": tool_name,
                    "arguments": args,
                    "result": None,
                }
                ordered_slots.append(slot)

                if tool_name == "create_event":
                    start_dt = _parse_iso_or_none(str(args.get("start")))
                    if start_dt and start_dt > max_add_event_datetime_local:
                        slot["result"] = {
                            "requires_confirmation": False,
                            "error": "event_beyond_one_year_range",
                            "message": "event beyond 1 year range",
                            "requested_start": args.get("start"),
                            "requested_end": args.get("end"),
                            "max_add_event_datetime_local": max_add_event_datetime_local.isoformat(),
                        }
                        continue

                signature = f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
                cached_result = tool_result_cache.get(signature)
                if cached_result is not None:
                    slot["result"] = cached_result
                    continue

                if tool_name == "search_web_for_events" and web_search_unavailable_in_turn:
                    slot["result"] = {
                        "query": "",
                        "search_results": [],
                        "documents_count": 0,
                        "events": [],
                        "events_count": 0,
                        "error": "web_search_unavailable",
                        "message": "Web search skipped due to earlier provider unavailability in this request.",
                        "short_circuited": True,
                    }
                    continue
                if tool_name == "search_web_for_events" and defer_web_fallback_in_batch:
                    slot["result"] = {
                        "query": str(args.get("subject") or ""),
                        "search_results": [],
                        "documents_count": 0,
                        "events": [],
                        "events_count": 0,
                        "error": "web_search_deferred",
                        "message": (
                            "Web search deferred because official-source lookup is in this same tool batch. "
                            "Fallback web search can run in a subsequent turn only if needed."
                        ),
                        "short_circuited": True,
                    }
                    continue

                fut = pool.submit(_invoke_tool_safe, tool_name, args)
                futures_map[fut] = idx

            for fut in as_completed(futures_map):
                slot_idx = futures_map[fut]
                ordered_slots[slot_idx]["result"] = fut.result()

        for slot in ordered_slots:
            result = slot["result"]
            tool_name = slot["name"]
            args = slot["arguments"]

            signature = f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
            if isinstance(result, dict):
                tool_result_cache[signature] = result
                if (
                    tool_name == "search_web_for_events"
                    and result.get("error") in {"web_search_unavailable", "tool_execution_failed"}
                ):
                    web_search_unavailable_in_turn = True

            tool_results.append(
                {
                    "id": slot["id"],
                    "name": tool_name,
                    "arguments": args,
                    "result": result,
                }
            )
            messages.append(
                ToolMessage(
                    content=json.dumps(result, default=str),
                    tool_call_id=slot["id"],
                )
            )

        try:
            response = await _ainvoke_model(messages)
        except Exception:
            if tool_results:
                break
            raise

    events_for_ui: List[Dict[str, Any]] = []
    delete_result: Dict[str, Any] | None = None
    delete_candidates: List[Dict[str, Any]] = []
    edit_result: Dict[str, Any] | None = None
    edit_candidates: List[Dict[str, Any]] = []
    edit_update_fields: Dict[str, Any] = {}
    edit_scope_value: str = "selected"
    staged_add_candidates: List[Dict[str, Any]] = []
    staged_payload_by_candidate_id: Dict[str, Dict[str, Any]] = {}
    blocked_add_candidates: List[Dict[str, Any]] = []
    add_window_errors: List[Dict[str, Any]] = []
    seen_event_keys: set[str] = set()
    for tr in tool_results:
        tool_name = tr.get("name")
        result = tr.get("result", {})
        if isinstance(result, dict):
            if tool_name in {"search_web_for_events", "search_official_sources"} and isinstance(result.get("events"), list):
                for item in result["events"]:
                    if not isinstance(item, dict):
                        continue
                    normalized = _normalize_web_event_candidate(item)
                    key = _event_dedupe_key(normalized)
                    if key in seen_event_keys:
                        continue
                    seen_event_keys.add(key)
                    events_for_ui.append(normalized)
            elif tool_name in {"create_event", "batch_create_events"}:
                if result.get("error") == "event_beyond_one_year_range":
                    add_window_errors.append(result)
                    continue
                candidates_to_process: List[tuple] = []
                if tool_name == "batch_create_events":
                    for cand in result.get("candidate_events", []):
                        if isinstance(cand, dict):
                            cid = cand.get("id")
                            pl = (result.get("event_payloads") or {}).get(str(cid)) if cid else None
                            candidates_to_process.append((cand, pl))
                else:
                    cand_single = result.get("candidate_event")
                    if isinstance(cand_single, dict):
                        candidates_to_process.append(
                            (cand_single, result.get("event_payload"))
                        )
                for candidate, payload in candidates_to_process:
                    candidate_start_dt = _parse_iso_datetime(str(candidate.get("start_iso")))
                    if (
                        candidate_start_dt is not None
                        and candidate_start_dt > max_add_event_datetime_local
                    ):
                        blocked_add_candidates.append(
                            {
                                "candidate": candidate,
                                "error": "outside_one_year_window",
                            }
                        )
                    else:
                        key = _event_dedupe_key(candidate)
                        if key not in seen_event_keys:
                            seen_event_keys.add(key)
                            events_for_ui.append(candidate)
                        staged_add_candidates.append(candidate)
                    candidate_id = candidate.get("id")
                    if candidate_id and isinstance(payload, dict):
                        staged_payload_by_candidate_id[str(candidate_id)] = payload
            elif isinstance(result.get("events"), list):
                for item in result["events"]:
                    if not isinstance(item, dict):
                        continue
                    key = _event_dedupe_key(item)
                    if key in seen_event_keys:
                        continue
                    seen_event_keys.add(key)
                    events_for_ui.append(item)
            elif isinstance(result.get("event"), dict):
                key = _event_dedupe_key(result["event"])
                if key not in seen_event_keys:
                    seen_event_keys.add(key)
                    events_for_ui.append(result["event"])
            if tool_name == "delete_calendar_events":
                delete_result = result
                if isinstance(result.get("candidates"), list):
                    delete_candidates.extend(result["candidates"])
            if tool_name == "edit_calendar_events":
                edit_result = result
                if isinstance(result.get("candidates"), list):
                    edit_candidates.extend(result["candidates"])
                if isinstance(result.get("update_fields"), dict):
                    edit_update_fields = result["update_fields"]
                if isinstance(result.get("edit_scope"), str):
                    edit_scope_value = result["edit_scope"]
    web_tool_errors = [
        tr.get("result")
        for tr in tool_results
        if tr.get("name") in {"search_web_for_events", "search_official_sources"}
        and isinstance(tr.get("result"), dict)
        and tr["result"].get("error")
    ]

    # Recovery path: if add-intent found external events but the model staged
    # none (e.g., batch_create_events called with an empty list), auto-stage
    # those external candidates so confirmation can still proceed.
    if primary_intent == "add" and not staged_add_candidates:
        for discovered in events_for_ui:
            if not isinstance(discovered, dict):
                continue
            if discovered.get("status") != "external_candidate":
                continue
            summary_text = str(discovered.get("summary") or "").strip()
            start_value = str(discovered.get("start_iso") or "").strip()
            end_value = str(discovered.get("end_iso") or "").strip()
            if not (summary_text and start_value and end_value):
                continue
            staged_result = _stage_single_event(
                summary=summary_text,
                start=start_value,
                end=end_value,
                timezone=str(discovered.get("timezone") or "UTC"),
                description=_build_external_candidate_description(discovered),
                event_options=default_event_options,
            )
            candidate = staged_result.get("candidate_event")
            payload = staged_result.get("event_payload")
            if not isinstance(candidate, dict):
                continue
            candidate_start_dt = _parse_iso_datetime(str(candidate.get("start_iso")))
            if candidate_start_dt is not None and candidate_start_dt > max_add_event_datetime_local:
                blocked_add_candidates.append(
                    {
                        "candidate": candidate,
                        "error": "outside_one_year_window",
                    }
                )
                continue
            staged_add_candidates.append(candidate)
            candidate_id = candidate.get("id")
            if candidate_id and isinstance(payload, dict):
                staged_payload_by_candidate_id[str(candidate_id)] = payload

    requires_delete_confirmation = bool(
        delete_result and delete_result.get("requires_confirmation")
    )
    requires_edit_confirmation = bool(
        edit_result and edit_result.get("requires_confirmation")
    )
    requires_add_confirmation = bool(staged_add_candidates)
    add_blocked_without_candidates = bool(blocked_add_candidates and not staged_add_candidates)
    add_error_without_candidates = bool(add_window_errors and not staged_add_candidates)

    action = _derive_action(tool_results)
    if requires_add_confirmation:
        action = "add_pending_confirmation"
    if requires_delete_confirmation:
        action = "delete_pending_confirmation"
    if requires_edit_confirmation:
        action = "edit_pending_confirmation"
    if (
        (add_blocked_without_candidates or add_error_without_candidates)
        and action not in {"delete_pending_confirmation", "delete"}
    ):
        action = "none"
    if primary_intent in {"add", "delete", "edit"} and action == "retrieve":
        write_tool_names = {"create_event", "batch_create_events", "delete_calendar_events", "edit_calendar_events"}
        has_write_tool_activity = any(
            str(tr.get("name")) in write_tool_names for tr in tool_results if isinstance(tr, dict)
        )
        has_write_path_outcome = any(
            [
                requires_add_confirmation,
                requires_delete_confirmation,
                requires_edit_confirmation,
                add_blocked_without_candidates,
                add_error_without_candidates,
                has_write_tool_activity,
            ]
        )
        if has_write_path_outcome:
            action = "none"
            events_for_ui = []
    if action == "add_pending_confirmation":
        confirmation_id = str(uuid4())
        PENDING_CONFIRMATIONS[confirmation_id] = {
            "operation": "add",
            "candidates": staged_add_candidates,
            "payload_by_candidate_id": staged_payload_by_candidate_id,
            "created_at_utc": runtime_context["current_datetime_utc"],
        }
        summary: Dict[str, Any] = {
            "calendar_id": runtime_context["default_calendar_id"],
            "operation": "add",
            "confirmation_id": confirmation_id,
            "candidate_count": len(staged_add_candidates),
            "candidates": staged_add_candidates,
            "blocked_count": len(blocked_add_candidates),
            "blocked": blocked_add_candidates,
            "max_add_event_datetime_local": max_add_event_datetime_local.isoformat(),
        }
        events_for_ui = staged_add_candidates
    elif action == "delete_pending_confirmation":
        confirmation_id = str(uuid4())
        PENDING_CONFIRMATIONS[confirmation_id] = {
            "operation": "delete",
            "candidates": delete_candidates,
            "delete_series": bool((delete_result or {}).get("delete_series", False)),
            "created_at_utc": runtime_context["current_datetime_utc"],
        }
        summary = {
            "calendar_id": runtime_context["default_calendar_id"],
            "operation": "delete",
            "confirmation_id": confirmation_id,
            "candidate_count": len(delete_candidates),
            "candidates": delete_candidates,
            "delete_series": bool((delete_result or {}).get("delete_series", False)),
            "not_found_count": int((delete_result or {}).get("not_found_count", 0)),
            "not_found": (delete_result or {}).get("not_found", []),
        }
        events_for_ui = delete_candidates
    elif action == "edit_pending_confirmation":
        confirmation_id = str(uuid4())
        PENDING_CONFIRMATIONS[confirmation_id] = {
            "operation": "edit",
            "candidates": edit_candidates,
            "update_fields": edit_update_fields,
            "edit_scope": edit_scope_value,
            "created_at_utc": runtime_context["current_datetime_utc"],
        }
        summary = {
            "calendar_id": runtime_context["default_calendar_id"],
            "operation": "edit",
            "confirmation_id": confirmation_id,
            "candidate_count": len(edit_candidates),
            "candidates": edit_candidates,
            "update_fields": edit_update_fields,
            "edit_scope": edit_scope_value,
            "series": (edit_result or {}).get("series", []),
            "series_count": int((edit_result or {}).get("series_count", 0)),
            "not_found_count": int((edit_result or {}).get("not_found_count", 0)),
            "not_found": (edit_result or {}).get("not_found", []),
            "diagnostics": (edit_result or {}).get("diagnostics", {}),
        }
        events_for_ui = edit_candidates
    elif action == "delete":
        requires_confirmation = bool((delete_result or {}).get("requires_confirmation"))
        if requires_confirmation:
            # Safety fallback: if classification ever misses, still return pending confirmation.
            action = "delete_pending_confirmation"
            confirmation_id = str(uuid4())
            PENDING_CONFIRMATIONS[confirmation_id] = {
                "operation": "delete",
                "candidates": delete_candidates,
                "delete_series": bool((delete_result or {}).get("delete_series", False)),
                "created_at_utc": runtime_context["current_datetime_utc"],
            }
            summary = {
                "calendar_id": runtime_context["default_calendar_id"],
                "operation": "delete",
                "confirmation_id": confirmation_id,
                "candidate_count": len(delete_candidates),
                "candidates": delete_candidates,
                "delete_series": bool((delete_result or {}).get("delete_series", False)),
                "not_found_count": int((delete_result or {}).get("not_found_count", 0)),
                "not_found": (delete_result or {}).get("not_found", []),
            }
            events_for_ui = delete_candidates
        else:
            summary = {
                "calendar_id": runtime_context["default_calendar_id"],
                "deleted_count": int((delete_result or {}).get("deleted_count", 0)),
                "deleted_events": (delete_result or {}).get("deleted_events", []),
                "not_found_count": int((delete_result or {}).get("not_found_count", 0)),
                "not_found": (delete_result or {}).get("not_found", []),
                "errors": (delete_result or {}).get("errors", []),
            }
    elif action == "create":
        summary = {
            "events_created_count": 0,
            "calendar_id": runtime_context["default_calendar_id"],
            "events_added": [],
        }
    elif action == "edit":
        summary = {
            "updated_count": int((edit_result or {}).get("updated_count", 0)),
            "updated_events": (edit_result or {}).get("updated_events", []),
            "calendar_id": runtime_context["default_calendar_id"],
            "not_found_count": int((edit_result or {}).get("not_found_count", 0)),
            "not_found": (edit_result or {}).get("not_found", []),
            "errors": (edit_result or {}).get("errors", []),
        }
    elif action == "retrieve":
        summary = {
            "events_found_count": len(events_for_ui),
            "calendar_id": runtime_context["default_calendar_id"],
            "events": events_for_ui,
        }
        if add_window_exceeds_one_year:
            summary["add_window_error"] = "requested_window_exceeds_one_year_limit"
            summary["max_add_event_datetime_local"] = max_add_event_datetime_local.isoformat()
    elif action == "mixed":
        summary = {
            "events_count": len(events_for_ui),
            "calendar_id": runtime_context["default_calendar_id"],
            "events": events_for_ui,
        }
    else:
        summary = {
            "events_count": 0,
            "calendar_id": runtime_context["default_calendar_id"],
            "events": [],
        }
        if add_blocked_without_candidates:
            summary["add_window_error"] = "requested_events_outside_one_year_limit"
            summary["blocked_count"] = len(blocked_add_candidates)
            summary["blocked"] = blocked_add_candidates
            summary["max_add_event_datetime_local"] = max_add_event_datetime_local.isoformat()
        if add_error_without_candidates:
            summary["add_window_error"] = "event_beyond_one_year_range"
            summary["error_message"] = "event beyond 1 year range"
            summary["errors"] = add_window_errors
            summary["max_add_event_datetime_local"] = max_add_event_datetime_local.isoformat()
        if primary_intent == "add" and not staged_add_candidates:
            if web_tool_errors:
                summary["error"] = "web_search_unavailable"
                summary["message"] = "Unable to discover matching public events from web providers."
                summary["provider_errors"] = web_tool_errors
            else:
                summary["error"] = "no_addable_events_found"
                summary["message"] = "No events were found to add for this request."
        elif primary_intent == "delete" and not delete_candidates:
            summary["error"] = "no_deletable_events_found"
            summary["message"] = "No matching events were found to delete for this request."
        elif primary_intent == "edit" and not edit_candidates:
            summary["error"] = "no_editable_events_found"
            summary["message"] = "No matching events were found to edit for this request."
            if isinstance(edit_result, dict) and edit_result.get("error"):
                summary["edit_error"] = edit_result.get("error")
                summary["edit_message"] = edit_result.get("message")

    return {
        "result_type": "calendar_events",
        "action": action,
        "summary": summary,
        "events": events_for_ui,
        "meta": {
            "default_calendar_id": runtime_context["default_calendar_id"],
            "current_datetime_utc": runtime_context["current_datetime_utc"],
            "current_datetime_local": runtime_context["current_datetime_local"],
            "query": message,
            "web_search_mode": web_search_mode,
            "resolved_time_window": resolved_time_window,
        },
        "tool_results": tool_results,
    }

