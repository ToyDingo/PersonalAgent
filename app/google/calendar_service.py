from __future__ import annotations

import copy
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List

from googleapiclient.discovery import build

from app.google.auth import get_calendar_credentials


WINDOWS_TO_IANA_TIMEZONE: Dict[str, str] = {
    "eastern standard time": "America/New_York",
    "eastern daylight time": "America/New_York",
    "central standard time": "America/Chicago",
    "central daylight time": "America/Chicago",
    "mountain standard time": "America/Denver",
    "mountain daylight time": "America/Denver",
    "pacific standard time": "America/Los_Angeles",
    "pacific daylight time": "America/Los_Angeles",
    "alaska standard time": "America/Anchorage",
    "alaska daylight time": "America/Anchorage",
    "hawaii standard time": "Pacific/Honolulu",
    "hawaii-aleutian standard time": "Pacific/Honolulu",
    "atlantic standard time": "America/Halifax",
    "atlantic daylight time": "America/Halifax",
    "gmt standard time": "Europe/London",
    "utc": "UTC",
}


def _normalize_timezone(tz: str | None) -> str:
    if not tz:
        return "UTC"
    lowered = tz.strip().lower()
    mapped = WINDOWS_TO_IANA_TIMEZONE.get(lowered)
    if mapped:
        return mapped
    return tz


def _get_calendar_service():
    creds = get_calendar_credentials()
    service = build("calendar", "v3", credentials=creds)
    return service


def _coerce_iso(start_or_end: Dict[str, Any], key: str) -> str | None:
    value = start_or_end.get(key)
    if value:
        return str(value)
    date_only = start_or_end.get("date")
    if date_only:
        return f"{date_only}T00:00:00+00:00"
    return None


def list_upcoming_events(
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """
    List upcoming events on the primary calendar.
    """
    service = _get_calendar_service()
    now = datetime.now(timezone.utc).isoformat()
    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = events_result.get("items", [])
    return events


def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    start_data = event.get("start", {})
    end_data = event.get("end", {})
    start_iso = _coerce_iso(start_data, "dateTime")
    end_iso = _coerce_iso(end_data, "dateTime")
    tz = start_data.get("timeZone") or end_data.get("timeZone") or "UTC"

    normalized = {
        "id": event.get("id"),
        "summary": event.get("summary"),
        "description": event.get("description"),
        "location": event.get("location"),
        "status": event.get("status"),
        "html_link": event.get("htmlLink"),
        "start_iso": start_iso,
        "end_iso": end_iso,
        "timezone": tz,
        "is_all_day": bool(start_data.get("date") and not start_data.get("dateTime")),
        "source_calendar": "primary",
    }
    if isinstance(event.get("reminders"), dict):
        normalized["reminders"] = event.get("reminders")
    if event.get("visibility") is not None:
        normalized["visibility"] = event.get("visibility")
    if event.get("colorId") is not None:
        normalized["color_id"] = event.get("colorId")
    if event.get("eventType") is not None:
        normalized["event_type"] = event.get("eventType")
    return normalized


def normalize_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_event(event) for event in events]


def _parse_iso_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _normalize_rfc3339(value: str | None) -> str | None:
    if value is None:
        return None
    return _parse_iso_datetime(value).isoformat()


def _event_start_datetime(event: Dict[str, Any]) -> datetime | None:
    start_data = event.get("start", {})
    date_time = start_data.get("dateTime")
    if date_time:
        try:
            return _parse_iso_datetime(date_time)
        except ValueError:
            return None

    date_only = start_data.get("date")
    if date_only:
        try:
            d = date.fromisoformat(date_only)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        except ValueError:
            return None

    return None


def _weekday_to_int(weekday: str) -> int:
    mapping = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    key = weekday.strip().lower()
    if key not in mapping:
        raise ValueError(
            "weekday must be one of: monday, tuesday, wednesday, thursday, "
            "friday, saturday, sunday"
        )
    return mapping[key]


def _next_weekday_dates(anchor_date: date, target_weekday: int, count: int) -> List[date]:
    dates: List[date] = []
    current = anchor_date
    while len(dates) < count:
        if current.weekday() == target_weekday:
            dates.append(current)
        current = current + timedelta(days=1)
    return dates


MATCH_THRESHOLD = 0.3


def _normalize_text_for_matching(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _title_match_score(query: str, event_summary: str) -> tuple[str, float]:
    if not query or not event_summary:
        return ("none", 0.0)

    q_lower = query.lower().strip()
    s_lower = event_summary.lower().strip()

    if q_lower == s_lower:
        return ("exact", 1.0)
    if q_lower in s_lower:
        return ("contains", 0.9)
    if s_lower in q_lower:
        return ("contains_reverse", 0.85)

    q_norm = _normalize_text_for_matching(query)
    s_norm = _normalize_text_for_matching(event_summary)

    if q_norm == s_norm:
        return ("normalized_exact", 0.95)
    if q_norm in s_norm:
        return ("normalized_contains", 0.85)
    if s_norm in q_norm:
        return ("normalized_contains_reverse", 0.8)

    q_tokens = set(q_norm.split())
    s_tokens = set(s_norm.split())
    if q_tokens and q_tokens.issubset(s_tokens):
        return ("token_subset", 0.8)
    if q_tokens and s_tokens:
        overlap = len(q_tokens & s_tokens)
        total = len(q_tokens)
        if overlap > 0:
            token_score = overlap / total * 0.7
            if token_score >= MATCH_THRESHOLD:
                return ("token_overlap", token_score)

    ratio = SequenceMatcher(None, q_norm, s_norm).ratio()
    if ratio >= 0.6:
        return ("fuzzy", ratio * 0.7)

    return ("none", 0.0)


def search_events(
    query: str | None = None,
    max_results: int = 10,
    start_time: str | None = None,
    end_time: str | None = None,
    weekday: str | None = None,
    count: int | None = None,
    allow_past: bool = False,
) -> List[Dict[str, Any]]:
    """
    Search calendar events with optional text and time filters.

    When Google's API text search (q=) returns zero results and a query string
    was provided, falls back to fetching all events in the time window and
    filtering locally with fuzzy title matching.

    Supports granular queries such as:
    - next N events in a series (via `query` + `max_results`)
    - events for next 2 Saturdays (via `weekday="saturday"` + `count=2`)
    """
    service = _get_calendar_service()
    now = datetime.now(timezone.utc)
    time_min = _normalize_rfc3339(start_time) or now.isoformat()
    time_max = _normalize_rfc3339(end_time)

    if not allow_past:
        min_dt = _parse_iso_datetime(time_min)
        if min_dt < now:
            time_min = now.isoformat()

        if time_max:
            max_dt = _parse_iso_datetime(time_max)
            if max_dt < now:
                return []

    fetch_limit = max(50, max_results)
    if weekday and count:
        fetch_limit = max(fetch_limit, 250)

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            q=query,
            maxResults=fetch_limit,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = events_result.get("items", [])

    if not events and query:
        all_events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=250,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        all_events = all_events_result.get("items", [])
        scored: list[tuple[Dict[str, Any], float]] = []
        for evt in all_events:
            summary_text = evt.get("summary", "")
            _, score = _title_match_score(query, summary_text)
            if score >= MATCH_THRESHOLD:
                scored.append((evt, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        events = [item[0] for item in scored]

    if weekday:
        weekday_int = _weekday_to_int(weekday)
        if count and count > 0:
            anchor_dt = _parse_iso_datetime(start_time) if start_time else now
            allowed_dates = set(
                _next_weekday_dates(anchor_dt.date(), weekday_int, count)
            )
            events = [
                event
                for event in events
                if (event_start := _event_start_datetime(event))
                and event_start.date() in allowed_dates
            ]
        else:
            events = [
                event
                for event in events
                if (event_start := _event_start_datetime(event))
                and event_start.weekday() == weekday_int
            ]

    return events[:max_results]


def delete_event_by_id(event_id: str) -> Dict[str, Any]:
    service = _get_calendar_service()
    try:
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return {"deleted": True, "event_id": event_id}
    except Exception:
        return {
            "deleted": False,
            "event_id": event_id,
            "error": "event_not_found",
        }


def resolve_delete_candidates(
    event_ids: List[str] | None = None,
    query: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_results: int = 20,
    allow_past: bool = False,
) -> Dict[str, Any]:
    """
    Resolve candidate events that may be deleted.

    This is used by the confirmation workflow before any destructive action.
    """
    candidates: List[Dict[str, Any]] = []
    not_found: List[Dict[str, Any]] = []

    if event_ids:
        service = _get_calendar_service()
        for event_id in event_ids:
            try:
                event = service.events().get(calendarId="primary", eventId=event_id).execute()
                candidates.append(event)
            except Exception:
                not_found.append({"event_id": event_id, "error": "event_not_found"})
    else:
        candidates = search_events(
            query=query,
            max_results=max_results,
            start_time=start_time,
            end_time=end_time,
            allow_past=allow_past,
        )

    return {
        "candidates": candidates,
        "not_found": not_found,
        "not_found_count": len(not_found),
    }


def delete_events(
    event_ids: List[str] | None = None,
    query: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_results: int = 20,
    delete_series: bool = False,
    allow_past: bool = False,
) -> Dict[str, Any]:
    """
    Delete calendar events by explicit IDs or by search query.

    Supports:
    - single event delete
    - multiple event delete
    - whole series delete (via recurringEventId) when delete_series=True
    """
    targets: List[Dict[str, Any]] = []

    if event_ids:
        service = _get_calendar_service()
        for event_id in event_ids:
            try:
                event = service.events().get(calendarId="primary", eventId=event_id).execute()
                targets.append(event)
            except Exception:
                targets.append({"id": event_id, "_missing": True})
    else:
        targets = search_events(
            query=query,
            max_results=max_results,
            start_time=start_time,
            end_time=end_time,
            allow_past=allow_past,
        )

    deleted_events: List[Dict[str, Any]] = []
    not_found: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    if not targets:
        return {
            "deleted_count": 0,
            "deleted_events": [],
            "not_found_count": 1,
            "not_found": [{"error": "event_not_found", "query": query}],
            "errors": [],
        }

    series_ids_deleted: set[str] = set()
    for event in targets:
        event_id = event.get("id")
        if not event_id:
            not_found.append({"error": "event_not_found", "event": event})
            continue

        if event.get("_missing"):
            not_found.append({"error": "event_not_found", "event_id": event_id})
            continue

        target_id = event_id
        if delete_series:
            recurring_id = event.get("recurringEventId")
            if recurring_id:
                target_id = recurring_id
                if target_id in series_ids_deleted:
                    continue
                series_ids_deleted.add(target_id)

        result = delete_event_by_id(target_id)
        if result.get("deleted"):
            deleted_events.append(
                {
                    "event_id": target_id,
                    "summary": event.get("summary"),
                    "is_series_delete": bool(delete_series and event.get("recurringEventId")),
                    "source_calendar": "primary",
                }
            )
        else:
            not_found.append(
                {
                    "event_id": target_id,
                    "summary": event.get("summary"),
                    "error": "event_not_found",
                }
            )

    return {
        "deleted_count": len(deleted_events),
        "deleted_events": deleted_events,
        "not_found_count": len(not_found),
        "not_found": not_found,
        "errors": errors,
    }


def resolve_edit_candidates(
    event_ids: List[str] | None = None,
    query: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_results: int = 20,
    allow_past: bool = False,
    include_series: bool = True,
) -> Dict[str, Any]:
    """
    Resolve candidate events for editing with multi-strategy matching.

    1. Tries Google API text search first (fast path).
    2. Falls back to fetching all events in window + local fuzzy matching.
    3. Groups recurring events by series.
    4. Returns match diagnostics for troubleshooting misses.
    """
    candidates: List[Dict[str, Any]] = []
    not_found: List[Dict[str, Any]] = []
    diagnostics: Dict[str, Any] = {
        "total_scanned": 0,
        "api_search_count": 0,
        "fallback_search_count": 0,
        "match_methods": {},
        "near_misses": [],
    }

    if event_ids:
        service = _get_calendar_service()
        for event_id in event_ids:
            try:
                event = (
                    service.events()
                    .get(calendarId="primary", eventId=event_id)
                    .execute()
                )
                candidates.append(event)
            except Exception:
                not_found.append({"event_id": event_id, "error": "event_not_found"})
        diagnostics["total_scanned"] = len(event_ids)
        diagnostics["api_search_count"] = len(candidates)
    else:
        api_results = search_events(
            query=query,
            max_results=max_results,
            start_time=start_time,
            end_time=end_time,
            allow_past=allow_past,
        )
        diagnostics["api_search_count"] = len(api_results)

        if api_results:
            candidates = api_results
            diagnostics["total_scanned"] = len(api_results)
            diagnostics["match_methods"]["api_direct"] = len(api_results)
        elif query:
            all_events = search_events(
                query=None,
                max_results=250,
                start_time=start_time,
                end_time=end_time,
                allow_past=allow_past,
            )
            diagnostics["total_scanned"] = len(all_events)
            diagnostics["fallback_search_count"] = len(all_events)

            scored: list[tuple[Dict[str, Any], str, float]] = []
            for evt in all_events:
                summary_text = evt.get("summary", "")
                method, score = _title_match_score(query, summary_text)
                if score >= MATCH_THRESHOLD:
                    scored.append((evt, method, score))
                elif score > 0.0:
                    diagnostics["near_misses"].append(
                        {
                            "summary": summary_text,
                            "match_method": method,
                            "score": round(score, 3),
                        }
                    )

            scored.sort(key=lambda x: x[2], reverse=True)
            candidates = [item[0] for item in scored[:max_results]]
            for _, method, _ in scored[:max_results]:
                diagnostics["match_methods"][method] = (
                    diagnostics["match_methods"].get(method, 0) + 1
                )
            diagnostics["near_misses"] = diagnostics["near_misses"][:5]

    series_groups: Dict[str, List[Dict[str, Any]]] = {}
    standalone: List[Dict[str, Any]] = []

    if include_series:
        for evt in candidates:
            recurring_id = evt.get("recurringEventId")
            if recurring_id:
                series_groups.setdefault(recurring_id, []).append(evt)
            else:
                standalone.append(evt)

    series_info: List[Dict[str, Any]] = []
    for series_id, members in series_groups.items():
        starts = [_event_start_datetime(m) for m in members]
        valid_starts = [s for s in starts if s is not None]
        series_info.append(
            {
                "recurring_event_id": series_id,
                "instance_count": len(members),
                "earliest_start": min(valid_starts).isoformat() if valid_starts else None,
                "latest_start": max(valid_starts).isoformat() if valid_starts else None,
                "sample_summary": members[0].get("summary") if members else None,
            }
        )

    return {
        "candidates": candidates,
        "candidate_count": len(candidates),
        "not_found": not_found,
        "not_found_count": len(not_found),
        "series": series_info,
        "series_count": len(series_info),
        "standalone_count": len(standalone),
        "diagnostics": diagnostics,
    }


def _build_event_patch_body(
    existing_event: Dict[str, Any],
    update_fields: Dict[str, Any],
) -> Dict[str, Any]:
    body: Dict[str, Any] = {}

    for scalar_key in (
        "summary",
        "description",
        "location",
        "visibility",
        "transparency",
        "colorId",
        "guestsCanInviteOthers",
        "guestsCanModify",
        "guestsCanSeeOtherGuests",
        "anyoneCanAddSelf",
        "eventType",
    ):
        if scalar_key in update_fields:
            body[scalar_key] = update_fields.get(scalar_key)

    for object_or_list_key in (
        "reminders",
        "attendees",
        "recurrence",
        "conferenceData",
        "attachments",
        "extendedProperties",
        "source",
    ):
        if object_or_list_key in update_fields and isinstance(
            update_fields.get(object_or_list_key), (dict, list)
        ):
            body[object_or_list_key] = copy.deepcopy(update_fields[object_or_list_key])

    timezone_value = update_fields.get("timezone")
    existing_start = existing_event.get("start", {}) if isinstance(existing_event, dict) else {}
    existing_end = existing_event.get("end", {}) if isinstance(existing_event, dict) else {}
    effective_timezone = _normalize_timezone(
        timezone_value
        or existing_start.get("timeZone")
        or existing_end.get("timeZone")
        or "UTC"
    )

    start_value = update_fields.get("start")
    end_value = update_fields.get("end")
    if start_value is not None:
        body["start"] = {"dateTime": start_value, "timeZone": effective_timezone}
    if end_value is not None:
        body["end"] = {"dateTime": end_value, "timeZone": effective_timezone}

    return body


def update_events_by_id(
    event_ids: List[str],
    update_fields: Dict[str, Any],
    update_series: bool = False,
) -> Dict[str, Any]:
    """
    Apply partial updates to existing events.

    When update_series is True, patches the recurring series master instead of
    individual instances (deduplicates by recurringEventId).
    """
    service = _get_calendar_service()
    updated_events: List[Dict[str, Any]] = []
    not_found: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    series_ids_updated: set[str] = set()

    for event_id in event_ids:
        try:
            existing = service.events().get(calendarId="primary", eventId=event_id).execute()
        except Exception:
            not_found.append({"event_id": event_id, "error": "event_not_found"})
            continue

        target_id = event_id
        if update_series:
            recurring_id = existing.get("recurringEventId")
            if recurring_id:
                target_id = recurring_id
                if target_id in series_ids_updated:
                    continue
                series_ids_updated.add(target_id)
                try:
                    existing = (
                        service.events()
                        .get(calendarId="primary", eventId=target_id)
                        .execute()
                    )
                except Exception:
                    errors.append(
                        {"event_id": target_id, "error": "series_master_not_found"}
                    )
                    continue

        patch_body = _build_event_patch_body(
            existing_event=existing, update_fields=update_fields
        )
        if not patch_body:
            errors.append({"event_id": target_id, "error": "no_update_fields"})
            continue

        try:
            updated = (
                service.events()
                .patch(calendarId="primary", eventId=target_id, body=patch_body)
                .execute()
            )
            result_event = normalize_event(updated)
            result_event["is_series_update"] = update_series and bool(
                existing.get("recurringEventId") or existing.get("recurrence")
            )
            updated_events.append(result_event)
        except Exception as exc:
            errors.append(
                {
                    "event_id": target_id,
                    "error": "update_failed",
                    "message": str(exc),
                }
            )

    return {
        "updated_count": len(updated_events),
        "updated_events": updated_events,
        "not_found_count": len(not_found),
        "not_found": not_found,
        "errors": errors,
    }


def _normalize_event_timezones(event_data: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("start", "end"):
        block = event_data.get(key)
        if not isinstance(block, dict):
            continue
        # Accept both Google-native `timeZone` and accidental lowercase `timezone`.
        if "timeZone" not in block and "timezone" in block:
            block["timeZone"] = block.get("timezone")
            block.pop("timezone", None)
        if "timeZone" in block:
            block["timeZone"] = _normalize_timezone(str(block["timeZone"]))
    return event_data


def create_or_update_event(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create or update an event on the primary calendar.

    If `id` is present in event_data we attempt to update that event,
    otherwise we insert a new one.
    """
    service = _get_calendar_service()
    calendar_id = "primary"
    event_data = _normalize_event_timezones(event_data)
    event_id = event_data.get("id")

    if event_id:
        updated = (
            service.events()
            .update(calendarId=calendar_id, eventId=event_id, body=event_data)
            .execute()
        )
        return updated

    created = (
        service.events()
        .insert(calendarId=calendar_id, body=event_data)
        .execute()
    )
    return created

