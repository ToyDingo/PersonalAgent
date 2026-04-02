from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.uploads.planner import _extract_candidates_via_ai


@pytest.mark.asyncio
async def test_extract_candidates_via_ai_image_uses_vision_and_detail_low(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def fake_create(**kwargs: object) -> MagicMock:
        captured["kwargs"] = kwargs
        messages = kwargs["messages"]
        user_content = messages[1]["content"]
        assert isinstance(user_content, list)
        img_parts = [p for p in user_content if isinstance(p, dict) and p.get("type") == "image_url"]
        assert len(img_parts) == 1
        assert img_parts[0]["image_url"]["detail"] == "low"
        assert "images" in (messages[0]["content"] or "").lower() or "screenshots" in (
            messages[0]["content"] or ""
        ).lower()

        payload = {
            "candidates": [
                {
                    "summary": "Team sync",
                    "start_iso": "2026-06-01T12:00:00-04:00",
                    "end_iso": "2026-06-01T13:00:00-04:00",
                    "timezone": "America/New_York",
                    "description": None,
                    "is_all_day": False,
                    "location": None,
                    "confidence": 0.9,
                    "source_excerpt": "Team sync",
                }
            ]
        }
        completion = MagicMock()
        completion.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
        return completion

    mock_client = MagicMock()
    mock_client.chat.completions.create = fake_create
    monkeypatch.setattr("app.uploads.planner.AsyncOpenAI", lambda api_key: mock_client)

    extracted = {
        "type": "image",
        "mime_type": "image/png",
        "content_base64": "aGVsbG8=",
    }
    result = await _extract_candidates_via_ai(
        extracted=extracted,
        user_message="Extract events from this flyer",
        default_timezone="America/New_York",
        now_local_iso="2026-04-02T12:00:00-04:00",
        openai_api_key="sk-test-key",
    )
    assert len(result) == 1
    assert result[0]["summary"] == "Team sync"
    assert captured["kwargs"]["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_extract_candidates_via_ai_text_uses_document_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create(**kwargs: object) -> MagicMock:
        messages = kwargs["messages"]
        assert "documents" in (messages[0]["content"] or "").lower()
        assert isinstance(messages[1]["content"], str)
        payload = {
            "candidates": [
                {
                    "summary": "Lunch",
                    "start_iso": "2026-06-01T12:00:00-04:00",
                    "end_iso": "2026-06-01T13:00:00-04:00",
                    "timezone": "America/New_York",
                    "description": None,
                    "is_all_day": False,
                    "location": None,
                    "confidence": 0.85,
                    "source_excerpt": "Lunch",
                }
            ]
        }
        completion = MagicMock()
        completion.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
        return completion

    mock_client = MagicMock()
    mock_client.chat.completions.create = fake_create
    monkeypatch.setattr("app.uploads.planner.AsyncOpenAI", lambda api_key: mock_client)

    extracted = {"type": "text", "content": "Meeting June 1 noon"}
    result = await _extract_candidates_via_ai(
        extracted=extracted,
        user_message="Add events",
        default_timezone="America/New_York",
        now_local_iso="2026-04-02T12:00:00-04:00",
        openai_api_key="sk-test-key",
    )
    assert len(result) == 1
    assert result[0]["summary"] == "Lunch"
