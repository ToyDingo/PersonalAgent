import base64
from pathlib import Path

import pytest

from app.uploads.extractors import extract_content_from_file


def test_extracts_text_file(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("Team sync at 10:00", encoding="utf-8")

    extracted = extract_content_from_file(str(file_path), ".txt")
    assert extracted["type"] == "text"
    assert "Team sync" in extracted["content"]


def test_extracts_ics_events(tmp_path: Path) -> None:
    file_path = tmp_path / "calendar.ics"
    file_path.write_text(
        "\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "BEGIN:VEVENT",
                "SUMMARY:Sprint Review",
                "DTSTART:20260320T150000Z",
                "DTEND:20260320T160000Z",
                "DESCRIPTION:Project milestone review",
                "END:VEVENT",
                "END:VCALENDAR",
            ]
        ),
        encoding="utf-8",
    )

    extracted = extract_content_from_file(str(file_path), ".ics")
    assert extracted["type"] == "ics_events"
    assert extracted["events"][0]["summary"] == "Sprint Review"


def test_extracts_image_to_base64(tmp_path: Path) -> None:
    file_path = tmp_path / "image.png"
    raw = b"\x89PNG\r\n\x1a\nmock"
    file_path.write_bytes(raw)

    extracted = extract_content_from_file(str(file_path), ".png")
    assert extracted["type"] == "image"
    assert extracted["mime_type"] == "image/png"
    assert extracted["content_base64"] == base64.b64encode(raw).decode("ascii")


def test_rejects_unknown_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "data.unknown"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported extractor extension"):
        extract_content_from_file(str(file_path), ".unknown")
