from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict
from uuid import uuid4

from app.uploads.types import UploadRecord

UPLOAD_MAX_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".xlsx", ".ics"}
UPLOAD_ROOT = Path("data/uploads")

_UPLOADS: Dict[str, UploadRecord] = {}
_UPLOADS_LOCK = Lock()


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_upload(record: UploadRecord, *, include_internal: bool = False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "upload_id": record.get("upload_id"),
        "filename": record.get("filename"),
        "content_type": record.get("content_type"),
        "extension": record.get("extension"),
        "size_bytes": record.get("size_bytes"),
        "status": record.get("status"),
        "error_code": record.get("error_code"),
        "error_message": record.get("error_message"),
        "created_at_utc": record.get("created_at_utc"),
        "updated_at_utc": record.get("updated_at_utc"),
    }
    if record.get("analysis") is not None:
        payload["analysis"] = record.get("analysis")
    if include_internal:
        payload["storage_path"] = record.get("storage_path")
    return payload


def create_upload_record(*, filename: str, content_type: str, file_bytes: bytes) -> Dict[str, Any]:
    file_name = (filename or "").strip()
    if not file_name:
        raise UploadValidationError("missing_filename", "Upload filename is required.")
    extension = Path(file_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(
            "unsupported_file_type",
            f"Unsupported file type '{extension}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )
    size_bytes = len(file_bytes)
    if size_bytes <= 0:
        raise UploadValidationError("empty_file", "Uploaded file is empty.")
    if size_bytes > UPLOAD_MAX_BYTES:
        raise UploadValidationError(
            "file_too_large",
            f"File exceeds maximum size of {UPLOAD_MAX_BYTES} bytes.",
        )

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid4())
    storage_path = UPLOAD_ROOT / f"{upload_id}{extension}"
    storage_path.write_bytes(file_bytes)
    now = _now_iso()

    record: UploadRecord = {
        "upload_id": upload_id,
        "filename": file_name,
        "content_type": content_type or "application/octet-stream",
        "extension": extension,
        "size_bytes": size_bytes,
        "storage_path": str(storage_path),
        "status": "uploaded",
        "error_code": None,
        "error_message": None,
        "analysis": None,
        "created_at_utc": now,
        "updated_at_utc": now,
    }
    with _UPLOADS_LOCK:
        _UPLOADS[upload_id] = record
    return _serialize_upload(record)


def get_upload_record(upload_id: str, *, include_internal: bool = False) -> Dict[str, Any] | None:
    with _UPLOADS_LOCK:
        record = _UPLOADS.get(upload_id)
        if record is None:
            return None
        return _serialize_upload(record, include_internal=include_internal)


def update_upload_status(
    upload_id: str,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> Dict[str, Any]:
    with _UPLOADS_LOCK:
        record = _UPLOADS.get(upload_id)
        if record is None:
            raise KeyError(upload_id)
        record["status"] = status
        record["error_code"] = error_code
        record["error_message"] = error_message
        record["updated_at_utc"] = _now_iso()
        return _serialize_upload(record, include_internal=True)


def store_upload_analysis(upload_id: str, analysis: Dict[str, Any]) -> Dict[str, Any]:
    with _UPLOADS_LOCK:
        record = _UPLOADS.get(upload_id)
        if record is None:
            raise KeyError(upload_id)
        record["analysis"] = analysis
        record["status"] = "analyzed"
        record["error_code"] = None
        record["error_message"] = None
        record["updated_at_utc"] = _now_iso()
        return _serialize_upload(record, include_internal=True)

