# Prompt Validation Backlog (Post-Workstream 2)

Generated from suite `v1.2` run on 2026-03-18.

## Current Status

- Total cases: 12
- Required cases: 9
- Optional cases: 3
- Required failures: 0
- Optional failures: 0
- Latency (ms): min 386 / p50 506 / p95 33218 / max 37201

## Resolved in Workstream 2

- **P1 resolved:** Generic retrieve prompts now use deterministic time-window fast-paths (sub-second in validation).
- **P2 resolved:** Delete-by-query prompts now use deterministic fast-path and stage confirmation directly.
- **P4 resolved:** Frontend now shows explicit no-match messaging for `action=none` with `no_*_found` errors.
- **P5 resolved:** Added explicit past-period regression coverage with `retrieve-last-month-explicit-past`.

## Remaining / Next Candidates

### P3 — External sports schedule latency remains high

- Cases still around 33-37s: `sports-disambiguation-atlanta-united`, `add-f1-races-official-source`.
- Root cause: external provider latency + LLM orchestration path.
- Next step: add richer in-progress UI statuses for external lookups and consider provider-level caching expansion.

### P6 — Confirmation confirm-path not covered in automated suite

- Current followups only validate `cancel`.
- Next step: add deterministic setup/teardown fixtures so a `confirm` followup can run safely and validate real writes in a controlled test calendar.

### P7 — Transient transport resiliency in prompt harness

- One run observed `ECONNRESET` before successful rerun.
- Next step: add bounded retry for connection-reset class errors in `run-prompt-validation.mjs` to reduce false negatives.

## Acceptance Notes

Workstream 2 implementation goals are met:
- deterministic router expansion implemented,
- prompt suite tightened and expanded,
- suite passes cleanly on rerun,
- backlog updated with resolved and remaining items.
