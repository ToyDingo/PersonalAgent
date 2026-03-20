from __future__ import annotations

import json
from pathlib import Path

from app.contracts import (
    AgentRequestContract,
    AgentResponseContract,
    UploadAnalyzeRequestContract,
    UploadErrorContract,
)


FIXTURES_DIR = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "contracts"
    / "fixtures"
)


def _load(name: str):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_agent_request_fixtures_match_python_contract() -> None:
    AgentRequestContract.model_validate(_load("agent-request.desktop.v1.json"))
    AgentRequestContract.model_validate(_load("agent-request.mobile.v1.json"))


def test_agent_response_fixtures_match_python_contract() -> None:
    for name in [
        "agent-response.v1.json",
        "agent-response.create.v1.json",
        "agent-response.reauth-required.v1.json",
        "agent-response.reauth-declined.v1.json",
        "agent-response.reauth-resumed-success.v1.json",
        "agent-upload-analysis-response.v1.json",
        "agent-upload-confirmed-success.v1.json",
        "agent-upload-confirmation-pending.v1.json",
    ]:
        AgentResponseContract.model_validate(_load(name))


def test_upload_request_and_error_fixtures_match_python_contract() -> None:
    UploadAnalyzeRequestContract.model_validate(_load("agent-upload-request.desktop.v1.json"))
    UploadErrorContract.model_validate(_load("agent-upload-error-unsupported-type.v1.json"))
