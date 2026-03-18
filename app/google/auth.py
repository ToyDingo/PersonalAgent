from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import settings


# For v1 we only need Calendar full access for a single user.
SCOPES = ["https://www.googleapis.com/auth/calendar"]
GOOGLE_CALENDAR_SERVICE_ID = "google_calendar"
GOOGLE_CALENDAR_SERVICE_NAME = "Google Calendar"
GOOGLE_CALENDAR_REAUTH_ENDPOINT = "/auth/google/start"


class ServiceAuthRequiredError(RuntimeError):
    def __init__(
        self,
        *,
        service: str,
        service_display_name: str,
        reauth_endpoint: str,
        reason: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.service_display_name = service_display_name
        self.reauth_endpoint = reauth_endpoint
        self.reason = reason


def _load_stored_credentials(token_path: Path) -> Tuple[Credentials | None, str | None]:
    if not token_path.exists():
        return None, "no_token"
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except Exception:
        return None, "invalid_token_file"

    if creds and creds.expired:
        if not creds.refresh_token:
            return None, "missing_refresh_token"
        try:
            creds.refresh(Request())
            with token_path.open("w", encoding="utf-8") as f:
                f.write(creds.to_json())
        except Exception as exc:
            message = str(exc).lower()
            if "invalid_grant" in message or "revoked" in message:
                return None, "token_revoked_or_expired"
            return None, "token_refresh_failed"

    if not creds or not creds.valid:
        return None, "invalid_or_expired"
    return creds, None


def _save_credentials(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with token_path.open("w", encoding="utf-8") as f:
        f.write(creds.to_json())


def get_google_auth_status() -> Dict[str, Any]:
    """
    Return whether Google credentials are available and roughly valid.
    """
    token_path = settings.google_token_file
    creds, reason = _load_stored_credentials(token_path)
    if not creds or reason:
        return {"authorized": False, "reason": reason or "invalid_or_expired"}

    return {"authorized": True}


def start_google_auth_flow() -> Dict[str, Any]:
    """
    Kick off an OAuth flow suitable for a single user running locally.

    We use Google's InstalledAppFlow with a local server redirect. From
    the caller's perspective, you will receive a URL to open and we will
    block until consent is complete, at which point tokens are written.

    For simplicity this is implemented as a blocking helper; the FastAPI
    endpoint that calls this should be used manually during setup.
    """
    client_secret_path = settings.google_client_secret_file
    if not client_secret_path.exists():
        raise FileNotFoundError(
            f"Google client secret file not found at {client_secret_path}"
        )

    # Use the standard installed app flow. This will open a browser or
    # return a URL to visit.
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path), SCOPES
    )
    creds = flow.run_local_server(
        port=0, prompt="consent", authorization_prompt_message=""
    )
    _save_credentials(creds, settings.google_token_file)

    return {
        "authorized": True,
        "message": "Google OAuth completed; tokens stored.",
        "service": GOOGLE_CALENDAR_SERVICE_ID,
        "service_display_name": GOOGLE_CALENDAR_SERVICE_NAME,
        "token_file": str(settings.google_token_file),
    }


def get_calendar_credentials() -> Credentials:
    """
    Ensure we have valid credentials for Calendar and return them.

    If credentials are missing or invalid, callers should first invoke
    the auth flow via /auth/google/start from a browser.
    """
    token_path = settings.google_token_file
    creds, reason = _load_stored_credentials(token_path)
    if not creds:
        if reason in {"token_revoked_or_expired", "missing_refresh_token", "token_refresh_failed"}:
            raise ServiceAuthRequiredError(
                service=GOOGLE_CALENDAR_SERVICE_ID,
                service_display_name=GOOGLE_CALENDAR_SERVICE_NAME,
                reauth_endpoint=GOOGLE_CALENDAR_REAUTH_ENDPOINT,
                reason=str(reason),
                message=(
                    "Google Calendar authorization has expired or was revoked. "
                    "Please re-authorize via /auth/google/start."
                ),
            )
        raise RuntimeError(
            "Google Calendar credentials are not available or invalid. "
            "Call /auth/google/start to authorize first."
        )
    return creds

