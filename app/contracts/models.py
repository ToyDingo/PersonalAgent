from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict


class _ContractContext(BaseModel):
    model_config = ConfigDict(extra="allow")
    contract_version: Literal["v1"]
    client_platform: str


class AgentRequestContract(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: str
    context: _ContractContext


class AgentResponseContract(BaseModel):
    model_config = ConfigDict(extra="allow")
    result_type: Literal["calendar_events"]
    action: str
    summary: Dict[str, Any]
    events: List[Dict[str, Any]]
    meta: Dict[str, Any]
    tool_results: List[Dict[str, Any]]


class UploadAnalyzeRequestContract(BaseModel):
    model_config = ConfigDict(extra="allow")
    upload_id: str
    message: str
    context: _ContractContext


class UploadErrorContract(BaseModel):
    model_config = ConfigDict(extra="allow")
    error: str
    message: str
