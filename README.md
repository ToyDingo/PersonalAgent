# PersonalAgent

An AI-powered Google Calendar assistant. Type natural-language commands ("add all Atlanta United MLS games for April", "delete my vet appointment next Tuesday", "rename all Vex Robotics events this year") and the agent translates them into Google Calendar operations — with a confirmation gate before any write is executed.

The application ships as a **Tauri desktop app** (Windows, macOS, Linux). The React/TypeScript frontend runs inside a native window via Tauri's system webview. The FastAPI Python backend runs locally alongside it and communicates with OpenAI and Google Calendar on the user's behalf.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | For the backend |
| Node.js | 18+ | For the frontend and Tauri CLI |
| Rust toolchain | stable | Required only for Tauri builds (`rustup`) |
| Visual Studio C++ Build Tools | 2019+ | Required only for Tauri builds on Windows |
| OpenAI API key | — | GPT-4o or equivalent |
| Google Cloud project | — | Calendar API enabled, OAuth 2.0 Desktop credentials |

---

## First-Time Setup

### 1. Environment variables

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-...
```

### 2. Google credentials

Create a Google Cloud project, enable the **Google Calendar API**, and create an **OAuth 2.0 client ID** for a Desktop application. Download the client secrets JSON and save it to:

```
data/google_client_secret.json
```

This file is git-ignored and must be placed manually on each machine.

### 3. Python backend dependencies

```powershell
# from project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 4. Frontend dependencies

```powershell
cd frontend
npm install
```

---

## Running in Development

Open two PowerShell terminals from the project root.

**Terminal 1 — Backend:**

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Backend runs at `http://127.0.0.1:8000`.

**Terminal 2 — Frontend (browser dev mode):**

```powershell
cd frontend
npm run dev
```

Frontend runs at `http://localhost:5173`.

### First-time Google authorization

Once the backend is running, authorize Google Calendar access:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/auth/google/start
```

This opens a browser tab for OAuth consent. After completing the flow, tokens are saved to `data/google_token.json` (git-ignored) and reused on subsequent runs.

To check authorization status at any time:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/auth/google/status
```

If the token expires or is revoked, the application will detect this automatically and prompt the user to re-authorize from within the UI.

---

## Document Upload Operations (Workstream 3)

You can upload an unstructured document plus a natural-language instruction (for example, "Add all dates in this document to my calendar"). The system extracts candidate operations and stages them through the same confirmation safety model used by chat writes.

Supported file types:

- `.txt`
- `.docx`
- `.pdf`
- `.xlsx`
- `.ics`
- `.png`
- `.jpg`
- `.jpeg`

Document flow:

1. Upload file (`POST /agent/uploads`)
2. Analyze and stage candidates (`POST /agent/uploads/{upload_id}/analyze`) with required JSON body:
   - `message` (required): user instruction for extraction intent
   - `timezone` (optional): default timezone fallback
3. Confirm/cancel via existing `operation_confirmation` flow on `/agent/chat`

No write operation executes until explicit confirmation.

---

## Building the Desktop App

The desktop installer is built with Tauri. This requires the Rust toolchain and Visual Studio C++ Build Tools to be installed.

```powershell
cd frontend
npm run tauri:build
```

Output installers (MSI and NSIS) are placed in:

```
frontend\src-tauri\target\release\bundle\
```

---

## QA Checks

Run all QA checks before building a release:

```powershell
cd frontend
npm run qa:desktop       # lint + contract fixture validation + TypeScript build
```

Run the prompt regression suite (requires the backend to be running):

```powershell
npm run prompt:validate
```

Optional: include an upload probe in the prompt validation run:

```powershell
$env:PROMPT_VALIDATION_UPLOAD_FILE_PATH="C:\Users\jacka\PersonalAgent\data\sample_upload_ops.txt"
npm run prompt:validate
Remove-Item Env:PROMPT_VALIDATION_UPLOAD_FILE_PATH
```

Results are written to `frontend/docs/reports/`.

---

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/config/test-openai` | Verifies OpenAI key is working |
| `GET` | `/auth/google/status` | Returns current Google Calendar authorization state |
| `GET` | `/auth/google/start` | Starts the Google OAuth 2.0 flow; blocks until complete |
| `GET` | `/calendar/events` | Lists upcoming events directly from Google Calendar |
| `POST` | `/calendar/events` | Creates or updates a single event directly |
| `POST` | `/agent/chat` | Main natural-language entrypoint; body: `{message, context}` |
| `POST` | `/agent/uploads` | Uploads a document (`txt`, `docx`, `pdf`, `xlsx`, `ics`, `png`, `jpg`, `jpeg`) and returns `upload_id` |
| `GET` | `/agent/uploads/{upload_id}` | Returns upload metadata, status, and analysis (if available) |
| `POST` | `/agent/uploads/{upload_id}/analyze` | Extracts operations from uploaded document using required `message` instruction and stages confirmation candidates |

All endpoints return JSON. Calendar endpoints return a structured `401 Unauthorized` with `service`, `reauth_endpoint`, and `message` fields if Google authorization has been lost.

---

## Key Files Reference

| File | Purpose |
|---|---|
| `app/main.py` | FastAPI app, all HTTP routes, CORS config |
| `app/agent/core.py` | LangChain agent loop, all tools, fast-paths, confirmation handling |
| `app/google/auth.py` | Google OAuth2 flow, token storage, `ServiceAuthRequiredError` |
| `app/google/calendar_service.py` | Google Calendar API CRUD wrappers and event normalization |
| `app/uploads/service.py` | Upload registry, lifecycle metadata, and storage management |
| `app/uploads/extractors.py` | Typed extraction adapters for text/image/ICS content (`txt`, `docx`, `pdf`, `xlsx`, `ics`, `png`, `jpg`, `jpeg`) |
| `app/uploads/planner.py` | AI-powered document operation planner (GPT-4o for text/image, deterministic ICS mapping) |
| `app/data_sources/sports.py` | ESPN, MLB Stats API, and Jolpica-F1 connectors |
| `app/data_sources/router.py` | Domain router for external data sources |
| `frontend/src/App.tsx` | Root React component, all UI state and request lifecycle |
| `frontend/src/api.ts` | Typed fetch wrappers for all backend API calls |
| `frontend/src/types.ts` | Shared TypeScript types (`AgentAction`, `CalendarEvent`, etc.) |
| `frontend/src/contracts/agentContract.ts` | Request builder and runtime response validator |
| `frontend/src/contracts/fixtures/` | JSON contract fixtures for CI validation |
| `frontend/scripts/run-prompt-validation.mjs` | Prompt regression test runner |
| `data/google_client_secret.json` | Google OAuth credentials (git-ignored, place manually) |
| `data/google_token.json` | Stored OAuth tokens (git-ignored, auto-generated) |
| `.env` | `OPENAI_API_KEY` (git-ignored) |
| `OPERATIONS_AND_ARCHITECTURE.md` | Full architecture and operational reference |
