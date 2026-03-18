# Prompt Validation Harness

This harness executes real `/agent/chat` prompts and validates response behavior for Workstream 1.

## Run

From `frontend/`:

```bash
npm run prompt:validate
```

Optional environment variables:

- `PROMPT_VALIDATION_API_BASE_URL` (default: `http://127.0.0.1:8000`)
- `PROMPT_VALIDATION_TIMEOUT_MS` (default: `90000`)

## Fixtures

Fixture file:

- `frontend/src/contracts/fixtures/prompt-validation.v1.json`

Each case supports:

- `id`
- `required` (optional, defaults true)
- `prompt`
- `context`
- `expectations`
  - `action_in`
  - `required_tools_any`
  - `forbidden_tools_any`
  - `resolved_source_contains`
  - `max_duration_ms`
- `followup`
  - currently supports `mode: "cancel"` and `expect_action_in`

## Output

Reports are written to:

- `frontend/docs/reports/prompt-validation.latest.json`
- `frontend/docs/reports/prompt-validation.latest.md`

The script exits non-zero if any **required** case fails.
