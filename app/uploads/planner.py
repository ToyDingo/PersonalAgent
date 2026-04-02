from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List
from uuid import uuid4

from openai import AsyncOpenAI

from app.uploads.types import ExtractedContent, IcsEvent

_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string"},
                    "start_iso": {"type": "string"},
                    "end_iso": {"type": "string"},
                    "timezone": {"type": "string"},
                    "description": {"type": ["string", "null"]},
                    "is_all_day": {"type": "boolean"},
                    "location": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "source_excerpt": {"type": "string"},
                },
                "required": [
                    "summary",
                    "start_iso",
                    "end_iso",
                    "timezone",
                    "description",
                    "is_all_day",
                    "location",
                    "confidence",
                    "source_excerpt",
                ],
            },
        }
    },
    "required": ["candidates"],
}


def _safe_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _intent_from_message(message: str) -> str:
    normalized = message.lower()
    if re.search(r"\b(delete|remove|cancel)\b", normalized):
        return "delete"
    if re.search(r"\b(edit|update|change|rename|reschedule|move)\b", normalized):
        return "edit"
    return "add"


def _is_date_only(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()))


def _coerce_iso(value: str) -> str:
    text = value.strip()
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def _ensure_all_day_end(start_iso: str, end_iso: str) -> str:
    if _is_date_only(start_iso) and _is_date_only(end_iso):
        try:
            start_date = date.fromisoformat(start_iso)
            end_date = date.fromisoformat(end_iso)
            if end_date <= start_date:
                return (start_date + timedelta(days=1)).isoformat()
        except ValueError:
            return end_iso
    return end_iso


def _build_add_payload(
    *,
    summary: str,
    start_iso: str,
    end_iso: str,
    timezone: str,
    description: str | None,
    location: str | None,
    is_all_day: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"summary": summary}
    if is_all_day and _is_date_only(start_iso) and _is_date_only(end_iso):
        payload["start"] = {"date": start_iso}
        payload["end"] = {"date": _ensure_all_day_end(start_iso, end_iso)}
    else:
        payload["start"] = {"dateTime": _coerce_iso(start_iso), "timeZone": timezone}
        payload["end"] = {"dateTime": _coerce_iso(end_iso), "timeZone": timezone}
    if description:
        payload["description"] = description
    if location:
        payload["location"] = location
    return payload


def _candidate_from_event(
    *,
    source_document_id: str,
    summary: str,
    start_iso: str,
    end_iso: str,
    timezone: str,
    description: str | None,
    location: str | None,
    source_excerpt: str,
    confidence: float,
    is_all_day: bool,
) -> Dict[str, Any]:
    return {
        "id": str(uuid4()),
        "operation": "add",
        "candidate_type": "document_candidate",
        "source_document_id": source_document_id,
        "source_excerpt": source_excerpt[:240],
        "confidence": max(0.0, min(1.0, float(confidence))),
        "parse_warnings": [],
        "summary": summary,
        "description": description,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "timezone": timezone,
        "payload": _build_add_payload(
            summary=summary,
            start_iso=start_iso,
            end_iso=end_iso,
            timezone=timezone,
            description=description,
            location=location,
            is_all_day=is_all_day,
        ),
    }


def _normalize_ics_event(
    *,
    source_document_id: str,
    event: IcsEvent,
    default_timezone: str,
    operation: str,
) -> Dict[str, Any] | None:
    summary = _safe_text(event.get("summary")) or "Document event"
    start_iso = _safe_text(event.get("dtstart"))
    end_iso = _safe_text(event.get("dtend"))
    timezone = _safe_text(event.get("timezone")) or default_timezone
    if not start_iso or not end_iso:
        return None
    if operation == "delete":
        # Delete path is query/time bounded because uploaded ICS does not include target event IDs.
        return {
            "id": str(uuid4()),
            "operation": "delete",
            "candidate_type": "document_candidate",
            "source_document_id": source_document_id,
            "source_excerpt": summary[:240],
            "confidence": 0.95,
            "parse_warnings": [],
            "summary": f"Delete matching event: {summary}",
            "description": None,
            "start_iso": start_iso,
            "end_iso": end_iso,
            "timezone": timezone,
            "delete_query": summary,
        }
    return _candidate_from_event(
        source_document_id=source_document_id,
        summary=summary,
        start_iso=start_iso,
        end_iso=end_iso,
        timezone=timezone,
        description=_safe_text(event.get("description")) or None,
        location=_safe_text(event.get("location")) or None,
        source_excerpt=summary,
        confidence=0.98,
        is_all_day=bool(event.get("is_all_day")),
    )


_SHARED_EXTRACTION_RULES = (
    "Return strict JSON only. "
    "Use the provided user instruction to decide which events to include. "
    "If no year is provided, infer the next upcoming occurrence relative to now_local_iso. "
    "If no explicit time is available, return all-day entries using YYYY-MM-DD for start_iso and end_iso. "
    "Prefer source timezone when present, otherwise use default_timezone. "
    "Skip events with ambiguous dates or insufficient schedule details."
)

_SYSTEM_PROMPT_TEXT = "You extract calendar-ready events from documents. " + _SHARED_EXTRACTION_RULES

_SYSTEM_PROMPT_VISION = (
    "You extract calendar-ready events from images: screenshots, photos of flyers or invitations, "
    "handwritten notes, and similar pictures. Read visible text and layout; infer dates and times "
    "when reasonable. When text is blurry or ambiguous, omit that event or use a lower confidence score. "
    + _SHARED_EXTRACTION_RULES
)


async def _extract_candidates_via_ai(
    *,
    extracted: ExtractedContent,
    user_message: str,
    default_timezone: str,
    now_local_iso: str,
    openai_api_key: str,
) -> list[Dict[str, Any]]:
    client = AsyncOpenAI(api_key=openai_api_key)
    user_prefix = (
        f"User instruction: {user_message}\n"
        f"default_timezone: {default_timezone}\n"
        f"now_local_iso: {now_local_iso}\n"
        "Return JSON object with a single key `candidates`."
    )
    if extracted["type"] == "image":
        system_prompt = _SYSTEM_PROMPT_VISION
        image_data_url = f"data:{extracted['mime_type']};base64,{extracted['content_base64']}"
        completion = await client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "document_event_candidates", "schema": _JSON_SCHEMA},
            },
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prefix},
                        {
                            "type": "image_url",
                            # detail=low reduces tokens/latency; sufficient for screenshots and flyers
                            "image_url": {"url": image_data_url, "detail": "low"},
                        },
                    ],
                },
            ],
        )
    else:
        system_prompt = _SYSTEM_PROMPT_TEXT
        completion = await client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "document_event_candidates", "schema": _JSON_SCHEMA},
            },
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_prefix + "\n\nDocument content:\n" + extracted["content"],
                },
            ],
        )
    content = (completion.choices[0].message.content or "").strip()
    parsed = json.loads(content) if content else {}
    raw_candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else []
    if not isinstance(raw_candidates, list):
        return []
    normalized: list[Dict[str, Any]] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        summary = _safe_text(item.get("summary"))
        start_iso = _safe_text(item.get("start_iso"))
        end_iso = _safe_text(item.get("end_iso"))
        timezone = _safe_text(item.get("timezone")) or default_timezone
        if not summary or not start_iso or not end_iso:
            continue
        normalized.append(
            {
                "summary": summary,
                "start_iso": start_iso,
                "end_iso": end_iso,
                "timezone": timezone,
                "description": _safe_text(item.get("description")) or None,
                "is_all_day": bool(item.get("is_all_day")),
                "location": _safe_text(item.get("location")) or None,
                "confidence": float(item.get("confidence") or 0.75),
                "source_excerpt": _safe_text(item.get("source_excerpt")) or summary,
            }
        )
    return normalized


async def plan_document_operations(
    *,
    extracted: ExtractedContent,
    user_message: str,
    source_document_id: str,
    default_timezone: str = "UTC",
    now_local_iso: str,
    openai_api_key: str,
) -> Dict[str, Any]:
    warnings: List[str] = []
    candidates: List[Dict[str, Any]] = []
    op_counts = {"add": 0, "edit": 0, "delete": 0}
    intent = _intent_from_message(user_message)

    if extracted["type"] == "ics_events":
        for event in extracted.get("events", []):
            candidate = _normalize_ics_event(
                source_document_id=source_document_id,
                event=event,
                default_timezone=default_timezone,
                operation="delete" if intent == "delete" else "add",
            )
            if candidate is None:
                warnings.append("Skipped ICS event missing dtstart/dtend.")
                continue
            candidates.append(candidate)
            op = str(candidate.get("operation", "add"))
            if op in op_counts:
                op_counts[op] += 1
        return {
            "analysis_status": "ready",
            "total_candidates": len(candidates),
            "operation_counts": op_counts,
            "warnings": warnings,
            "candidates": candidates,
        }

    if not openai_api_key.strip():
        raise ValueError("OPENAI_API_KEY is required for non-ICS document analysis.")

    ai_candidates = await _extract_candidates_via_ai(
        extracted=extracted,
        user_message=user_message,
        default_timezone=default_timezone,
        now_local_iso=now_local_iso,
        openai_api_key=openai_api_key,
    )
    for item in ai_candidates:
        try:
            candidate = _candidate_from_event(
                source_document_id=source_document_id,
                summary=_safe_text(item.get("summary")),
                start_iso=_safe_text(item.get("start_iso")),
                end_iso=_safe_text(item.get("end_iso")),
                timezone=_safe_text(item.get("timezone")) or default_timezone,
                description=_safe_text(item.get("description")) or None,
                location=_safe_text(item.get("location")) or None,
                source_excerpt=_safe_text(item.get("source_excerpt")),
                confidence=float(item.get("confidence") or 0.75),
                is_all_day=bool(item.get("is_all_day")),
            )
            candidates.append(candidate)
            op_counts["add"] += 1
        except Exception as exc:
            warnings.append(f"Skipped malformed AI candidate: {exc}")

    return {
        "analysis_status": "ready",
        "total_candidates": len(candidates),
        "operation_counts": op_counts,
        "warnings": warnings,
        "candidates": candidates,
    }

