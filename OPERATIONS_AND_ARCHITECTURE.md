# PersonalAgent Operations and Architecture

This document captures how to start and stop the application today, plus the current system architecture and behavior.

## Stack Snapshot

- Backend: FastAPI (`app/main.py`)
- Agent orchestration: LangChain tools + OpenAI chat model (`app/agent/core.py`)
- Calendar integration: Google Calendar API (`app/google/calendar_service.py`, `app/google/auth.py`)
- Frontend: React + TypeScript + Vite (`frontend/`)
- Local auth/token files:
  - `data/google_client_secret.json`
  - `data/google_token.json`

## Startup Commands (PowerShell)

Open two terminals from the project root (`C:\Users\jacka\PersonalAgent`).

### Terminal 1: Backend

```powershell
# from C:\Users\jacka\PersonalAgent
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Backend URL: `http://127.0.0.1:8000`

Useful checks:

```powershell
# health
Invoke-RestMethod http://127.0.0.1:8000/health

# google auth status
Invoke-RestMethod http://127.0.0.1:8000/auth/google/status
```

### Terminal 2: Frontend

```powershell
# from C:\Users\jacka\PersonalAgent
cd frontend
npm install
npm run dev
```

Frontend URL: `http://localhost:5173`

## Shutdown Commands

### Standard shutdown (recommended)

In each running terminal:

- Press `Ctrl + C` once to stop the running process.
- If prompted with `Terminate batch job (Y/N)?`, press `Y`.

Then optionally:

```powershell
# if venv is active
deactivate
```

### If a process is stuck (optional)

```powershell
# find process using backend port
netstat -ano | Select-String ":8000"

# find process using frontend port
netstat -ano | Select-String ":5173"

# kill by PID
taskkill /PID <PID> /F
```

## Roadmap (Phases 1-4)

### Phase 1 - Performance (in progress)

Completed optimizations:
- **Batch event creation:** New `batch_create_events` tool stages all events in a single tool call instead of N individual `create_event` calls. Reduces LLM output tokens and tool-loop overhead.
- **Parallel tool execution:** Within each LLM turn, all tool calls run concurrently via `ThreadPoolExecutor` instead of sequentially.
- **Official source result caching:** `search_sports_events` results are cached in-memory (5 min TTL) keyed on `(subject, start_time, end_time, timezone)`. Repeated queries return instantly.
- **Prompt-driven roundtrip reduction:** System prompt instructs the LLM to call `batch_create_events` in the same response as the search tool, collapsing 3 LLM roundtrips to 2.
- **Async LLM invocation:** Switched from `model.invoke()` to `await model.ainvoke()` to avoid blocking the event loop.

Earlier optimizations (from initial Phase 1 work):
- Instrument end-to-end timing (`discovery`, `document fetch`, `extraction`, `total`).
- Enforce strict latency budgets and fail fast when providers are unavailable.
- Reduce redundant work in a single turn (cache + short-circuit repeated web calls).
- Improve practical speed by shrinking extraction payload and capping extraction timeout.

### Phase 2 - Edit Existing Events (complete)

- Add update/edit capability for existing events (title, time, location, description).
- Use the same candidate + confirmation safety model used by add/delete when ambiguous.
- Multi-strategy event matching: Google API search → fallback to local fuzzy matching (exact, contains, token overlap, SequenceMatcher).
- Series-aware resolution: groups candidates by `recurringEventId`, supports `edit_scope` of `selected` (instances) or `series` (master).
- Returns match diagnostics (`total_scanned`, `match_methods`, `near_misses`) for troubleshooting zero-result queries.
- Confirmation flow stores `edit_scope` and `update_fields`; confirmed edits use `events.patch()` for partial updates.

### Phase 3 - Official Data Sources (complete)

- New LangChain tool `search_official_sources` that the LLM calls before `search_web_for_events` for sports queries.
- Multi-provider architecture with fallback chains per league:
  - **ESPN** (unofficial, free, no key): MLB, NFL, NBA, NHL, MLS, WNBA, UFC, PGA, college sports.
  - **MLB Stats API** (official, free, no key): MLB schedule fallback when ESPN is unavailable.
  - **Jolpica-F1** (free, no key): Formula 1 race calendar (successor to Ergast).
- Team resolution: dynamic fetch from ESPN teams endpoint + fuzzy name matching (cached 24 h).
- Event normalization: all providers produce the same event shape as web search, so the rest of the pipeline (create_event, confirmation flow, UI) is unchanged.
- Routing: `app/data_sources/router.py` → `app/data_sources/sports.py`. Extensible — add future connectors (music, movies) by registering in the router.
- System prompt updated: LLM is instructed to try `search_official_sources` first, fall back to `search_web_for_events` when source is `not_covered` or events_count is 0.

### Phase 4 - Simple Request Relevance Guardrails

Problem addressed:
- Simple requests can incorrectly return a large unrelated payload (for example 200 upcoming events) when a write-intent flow fails.

Guardrails:
- Detect primary intent (`add`, `delete`, `retrieve`) early.
- Block fallback from write intents (`add`/`delete`) to `retrieve` results.
- Return structured failures for write-intent misses (`web_search_unavailable`, `no_addable_events_found`, `no_deletable_events_found`) instead of broad retrieval data.
- Keep responses scoped to the user ask and avoid unrelated event dumps.

## Current Architecture and Design

## 1) API Layer (`app/main.py`)

Core endpoints:

- `GET /health`: basic service health
- `GET /config/test-openai`: validates OpenAI connectivity
- `GET /auth/google/status`: token/authorization status
- `GET /auth/google/start`: starts local OAuth flow and persists token
- `GET /calendar/events`: upcoming events (direct API helper)
- `POST /calendar/events`: create/update event payload passthrough
- `POST /agent/chat`: primary natural-language entrypoint

The frontend sends user messages to `/agent/chat` with optional `context`.

## 2) Agent Orchestration (`app/agent/core.py`)

The agent binds eight tools:

- `get_upcoming_events`
- `search_calendar_events`
- `search_official_sources`
- `search_web_for_events`
- `create_event`
- `batch_create_events`
- `delete_calendar_events`
- `edit_calendar_events`

Flow:

1. Build runtime context (current timestamps, default calendar, past-intent detection).
2. Handle delete confirmation context early if present.
3. Ask model to choose tools.
4. Execute tool calls with cleaned/sanitized arguments.
5. Return structured JSON:
   - `action` (`create`, `retrieve`, `delete`, `delete_pending_confirmation`, `delete_cancelled`, `mixed`, `none`)
   - `summary`
   - `events`
   - `meta`
   - `tool_results`

External-data behavior:

- For sports/racing schedules, the agent calls `search_official_sources` first (ESPN, MLB Stats API, Jolpica-F1).
- If the domain is not covered or the API returns no events, the agent falls back to `search_web_for_events`.
- Both tools return the same calendar-ready candidate shape (`summary`, `start`, `end`, `timezone`, `description`, `source_url`).
- The agent uses `batch_create_events` (preferred) or `create_event` to stage one or many events for user confirmation.

## 3) Calendar Service (`app/google/calendar_service.py`)

Responsibilities:

- Event retrieval (`list_upcoming_events`, `search_events`)
- Event normalization for UI (`normalize_event`, `normalize_events`)
- Candidate resolution for safe deletes (`resolve_delete_candidates`)
- Actual deletion (`delete_events`, `delete_event_by_id`)
- Event create/update (`create_or_update_event`)

Search supports filtering with combinations of:

- text query (`query`)
- time range (`start_time`, `end_time`)
- weekday and repeating weekday count
- future-only default unless explicitly past-oriented

## 4) Delete Safety Design (Current)

Deletion is intentionally two-step:

1. **Candidate identification**:
   - `delete_calendar_events` resolves possible matches and returns:
     - `requires_confirmation: true`
     - `candidates`
     - `candidate_count`
     - `not_found`
2. **User confirmation**:
   - Pending state stored in-memory (`PENDING_DELETE_CONFIRMATIONS`)
   - Frontend sends confirmation context:
     - `context.delete_confirmation.action = "confirm"` with selected IDs
     - or `action = "cancel"`
3. **Execution**:
   - On confirm, backend calls `delete_events(...)`
   - Returns structured delete result (`deleted_count`, `deleted_events`, `not_found_count`, `errors`)

Important behavior:

- If a delete tool result requires confirmation, response action is forced to `delete_pending_confirmation` (this avoids accidental `mixed` action masking).

## 5) Frontend Contract (Current)

`frontend/src/types.ts` models backend response as:

- `result_type: "calendar_events"`
- `action: AgentAction` (includes delete confirmation states)
- `summary: Record<string, unknown>`
- `events: CalendarEvent[]`
- `meta`
- `tool_results`

UI is expected to:

- Render candidate list when `action = "delete_pending_confirmation"`.
- Send confirm/cancel context back to `/agent/chat`.
- Render final JSON results for confirmed/cancelled delete outcomes.

