from __future__ import annotations

from typing import Any, Dict, Literal, TypedDict


UploadStatus = Literal["uploaded", "analyzing", "analyzed", "error"]
OperationKind = Literal["add", "edit", "delete"]


class UploadRecord(TypedDict, total=False):
    upload_id: str
    filename: str
    content_type: str
    extension: str
    size_bytes: int
    storage_path: str
    status: UploadStatus
    error_code: str | None
    error_message: str | None
    analysis: Dict[str, Any] | None
    created_at_utc: str
    updated_at_utc: str


class DocumentOperationCandidate(TypedDict, total=False):
    id: str
    operation: OperationKind
    candidate_type: str
    source_document_id: str
    source_excerpt: str
    confidence: float
    parse_warnings: list[str]
    summary: str | None
    description: str | None
    start_iso: str | None
    end_iso: str | None
    timezone: str | None
    target_event_id: str | None
    payload: Dict[str, Any] | None
    update_fields: Dict[str, Any] | None


class IcsEvent(TypedDict, total=False):
    summary: str | None
    dtstart: str | None
    dtend: str | None
    description: str | None
    location: str | None
    timezone: str | None
    is_all_day: bool


class TextExtractedContent(TypedDict):
    type: Literal["text"]
    content: str


class ImageExtractedContent(TypedDict):
    type: Literal["image"]
    content_base64: str
    mime_type: str


class IcsExtractedContent(TypedDict):
    type: Literal["ics_events"]
    events: list[IcsEvent]


ExtractedContent = TextExtractedContent | ImageExtractedContent | IcsExtractedContent

