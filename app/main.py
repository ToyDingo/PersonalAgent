from datetime import datetime

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.llm.openai_client import OpenAIClient
from app.google.auth import (
    ServiceAuthRequiredError,
    get_google_auth_status,
    start_google_auth_flow,
)
from app.google.calendar_service import (
    list_upcoming_events,
    create_or_update_event,
)
from app.agent.core import run_agent_chat, stage_document_candidates_for_confirmation
from app.uploads.extractors import extract_content_from_file
from app.uploads.planner import plan_document_operations
from app.uploads.service import (
    UploadValidationError,
    create_upload_record,
    get_upload_record,
    store_upload_analysis,
    update_upload_status,
)


app = FastAPI(title="Personal AI Assistant Backend")
ALLOWED_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    # Tauri desktop origins (dev + bundled app)
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_CORS_ORIGINS,
    # Keep desktop bundles working even when runtime emits custom/local origins.
    allow_origin_regex=r"^(null|tauri://localhost|https?://tauri\.localhost|https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

llm_client = OpenAIClient(api_key=settings.openai_api_key)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/config/test-openai")
async def test_openai() -> dict:
    try:
        reply = await llm_client.simple_ping()
    except Exception as exc:  # pragma: no cover - debug surface
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "echo": reply}


@app.get("/auth/google/status")
async def auth_status() -> dict:
    status = get_google_auth_status()
    return status


@app.get("/auth/google/start")
async def auth_start() -> JSONResponse:
    """
    Initiates the Google OAuth flow.

    For v1 this returns a URL you should open in a browser and a hint
    about what to expect. After completing consent, tokens will be stored
    locally and future calls will use them automatically.
    """
    try:
        data = start_google_auth_flow()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Google client secret file not found. "
                "Place it at the configured path and try again. "
                f"Details: {exc}"
            ),
        )
    return JSONResponse(content=data)


@app.get("/calendar/events")
async def get_events(
    max_results: int = 10,
) -> dict:
    try:
        events = list_upcoming_events(max_results=max_results)
        return {"events": events}
    except ServiceAuthRequiredError as exc:
        return JSONResponse(
            status_code=401,
            content={
                "error": "service_auth_required",
                "service": exc.service,
                "service_display_name": exc.service_display_name,
                "reauth_endpoint": exc.reauth_endpoint,
                "message": str(exc),
            },
        )


@app.post("/calendar/events")
async def post_event(payload: dict) -> dict:
    """
    Create or update a calendar event.
    Payload should contain at minimum summary, start, end.
    """
    try:
        event = create_or_update_event(payload)
        return {"event": event}
    except ServiceAuthRequiredError as exc:
        return JSONResponse(
            status_code=401,
            content={
                "error": "service_auth_required",
                "service": exc.service,
                "service_display_name": exc.service_display_name,
                "reauth_endpoint": exc.reauth_endpoint,
                "message": str(exc),
            },
        )


@app.post("/agent/chat")
async def agent_chat(payload: dict) -> dict:
    """
    Main natural-language interface.
    Body shape:
    {
      "message": "What is on my calendar tomorrow?",
      "context": {...optional...}
    }
    """
    message = payload.get("message")
    context = payload.get("context") or {}
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    result = await run_agent_chat(
        llm_client=llm_client,
        message=message,
        context=context,
    )
    return result


@app.post("/agent/uploads")
async def upload_document(file: UploadFile = File(...)) -> dict:
    try:
        raw = await file.read()
        record = create_upload_record(
            filename=file.filename or "",
            content_type=file.content_type or "application/octet-stream",
            file_bytes=raw,
        )
        return {
            "upload_id": record.get("upload_id"),
            "status": record.get("status"),
            "filename": record.get("filename"),
            "content_type": record.get("content_type"),
            "extension": record.get("extension"),
            "size_bytes": record.get("size_bytes"),
            "created_at_utc": record.get("created_at_utc"),
        }
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": exc.code, "message": str(exc)})


@app.get("/agent/uploads/{upload_id}")
async def get_upload(upload_id: str) -> dict:
    record = get_upload_record(upload_id=upload_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "upload_not_found", "upload_id": upload_id})
    return record


@app.post("/agent/uploads/{upload_id}/analyze")
async def analyze_upload(upload_id: str, payload: dict | None = None) -> dict:
    record = get_upload_record(upload_id=upload_id, include_internal=True)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "upload_not_found", "upload_id": upload_id})

    body = payload or {}
    default_timezone = str(body.get("timezone") or "UTC")
    user_message = str(body.get("message") or "").strip()
    if not user_message:
        raise HTTPException(
            status_code=400,
            detail={"error": "message_required", "message": "message is required for document analysis."},
        )
    try:
        update_upload_status(upload_id, status="analyzing")
        extracted = extract_content_from_file(
            path_str=str(record.get("storage_path")),
            extension=str(record.get("extension")),
        )
        analysis = await plan_document_operations(
            extracted=extracted,
            user_message=user_message,
            source_document_id=upload_id,
            default_timezone=default_timezone,
            now_local_iso=datetime.now().astimezone().isoformat(),
            openai_api_key=settings.openai_api_key,
        )
        store_upload_analysis(upload_id=upload_id, analysis=analysis)
        return stage_document_candidates_for_confirmation(
            upload_id=upload_id,
            filename=str(record.get("filename") or ""),
            analysis=analysis,
        )
    except FileNotFoundError:
        update_upload_status(
            upload_id,
            status="error",
            error_code="upload_file_missing",
            error_message="Uploaded file was not found on disk.",
        )
        raise HTTPException(
            status_code=404,
            detail={"error": "upload_file_missing", "upload_id": upload_id},
        )
    except ValueError as exc:
        update_upload_status(
            upload_id,
            status="error",
            error_code="document_extraction_failed",
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=400,
            detail={"error": "document_extraction_failed", "message": str(exc)},
        )
    except Exception as exc:
        update_upload_status(
            upload_id,
            status="error",
            error_code="document_analysis_failed",
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "document_analysis_failed", "message": str(exc)},
        )

