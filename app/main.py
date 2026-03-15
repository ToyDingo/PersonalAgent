from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.llm.openai_client import OpenAIClient
from app.google.auth import get_google_auth_status, start_google_auth_flow
from app.google.calendar_service import (
    list_upcoming_events,
    create_or_update_event,
)
from app.agent.core import run_agent_chat


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
    events = list_upcoming_events(max_results=max_results)
    return {"events": events}


@app.post("/calendar/events")
async def post_event(payload: dict) -> dict:
    """
    Create or update a calendar event.
    Payload should contain at minimum summary, start, end.
    """
    event = create_or_update_event(payload)
    return {"event": event}


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

