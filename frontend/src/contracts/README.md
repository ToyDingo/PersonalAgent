# API Contract Parity

This folder holds client-side contract helpers and fixtures used to keep
desktop and mobile behavior aligned.

## Rules

- Desktop and mobile clients must send the same message/context shape.
- Backend responses are validated against the same baseline contract checks.
- Any schema evolution should bump contract version and update fixtures.

## Current Contract Version

- `v1` (defined in `agentContract.ts`)

## Fixture Coverage

- `agent-request.desktop.v1.json`: canonical desktop request payload.
- `agent-request.mobile.v1.json`: canonical mobile request payload with identical shape.
- `agent-response.v1.json`: pending-confirmation response example.
- `agent-response.create.v1.json`: create-success response example.

## Validation

Run the fixture validator before desktop release builds:

`npm run contract:check`

For full desktop regression checks:

`npm run qa:desktop`

