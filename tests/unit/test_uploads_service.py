from pathlib import Path

import pytest

import app.uploads.service as upload_service
from app.uploads.service import UploadValidationError


@pytest.fixture(autouse=True)
def reset_upload_state(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_service, "UPLOAD_ROOT", Path(tmp_path) / "uploads")
    upload_service._UPLOADS.clear()
    yield
    upload_service._UPLOADS.clear()


def test_create_upload_record_success() -> None:
    payload = upload_service.create_upload_record(
        filename="notes.txt",
        content_type="text/plain",
        file_bytes=b"hello world",
    )

    assert payload["filename"] == "notes.txt"
    assert payload["extension"] == ".txt"
    assert payload["size_bytes"] == 11
    assert payload["status"] == "uploaded"

    stored = upload_service.get_upload_record(payload["upload_id"], include_internal=True)
    assert stored is not None
    assert Path(stored["storage_path"]).exists()


def test_create_upload_record_rejects_unsupported_type() -> None:
    with pytest.raises(UploadValidationError) as exc:
        upload_service.create_upload_record(
            filename="malware.exe",
            content_type="application/octet-stream",
            file_bytes=b"binary",
        )
    assert exc.value.code == "unsupported_file_type"


def test_create_upload_record_rejects_empty_file() -> None:
    with pytest.raises(UploadValidationError) as exc:
        upload_service.create_upload_record(
            filename="empty.txt",
            content_type="text/plain",
            file_bytes=b"",
        )
    assert exc.value.code == "empty_file"


def test_update_upload_status_unknown_id_raises_key_error() -> None:
    with pytest.raises(KeyError):
        upload_service.update_upload_status("does-not-exist", status="error")
