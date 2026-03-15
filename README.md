# Personal AI Assistant Backend

This is a local FastAPI-based backend for a personal AI assistant that talks to OpenAI and Google Calendar on your behalf.

## Setup

1. **Create and activate a virtual environment** (optional but recommended).
2. **Install dependencies**:

```bash
pip install -r requirements.txt
```

3. **Set environment variables**:

- `OPENAI_API_KEY` – your OpenAI API key.

4. **Google credentials**:

- Create a Google Cloud project, enable the Google Calendar API, and create OAuth client credentials for an installed/desktop app.
- Download the client secrets JSON file and save it as:
  - `data/google_client_secret.json`

5. **Run the server**:

```bash
uvicorn app.main:app --reload
```

## First-time Google auth

Once the server is running:

- Call `GET /auth/google/start` to initiate Google OAuth. Follow the returned URL and instructions to grant access.
- The resulting tokens will be stored in `data/google_token.json` (git-ignored) for reuse.

## Endpoints (high level)

- `GET /health` – health check.
- `GET /config/test-openai` – verifies OpenAI key works.
- `GET /auth/google/status` – whether Google Calendar auth is configured.
- `GET /calendar/events` – list upcoming events.
- `POST /calendar/events` – create/update events.
- `POST /agent/chat` – natural-language interface to your assistant.

## Frontend UI (React + TypeScript)

A simple single-user UI is available in `frontend/`.

1. Start backend:

```bash
uvicorn app.main:app --reload
```

2. Start frontend:

```bash
cd frontend
npm install
npm run dev
```

3. Open:

- `http://localhost:5173`

The UI posts to `/agent/chat` and displays JSON responses, event lists, and action-specific summary fields.


