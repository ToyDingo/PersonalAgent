# Desktop Release Checklist

## Build and Contract Gate

0. Install Rust toolchain (required for Tauri): `rustup default stable`.
1. Set backend endpoint in `.env` from `.env.example`.
2. Run `npm run qa:desktop`.
3. Run `npm run tauri:build` to verify bundling.

## Tauri Store Readiness

1. Replace generated icons in `src-tauri/icons` if branding updates are needed.
2. Confirm `src-tauri/tauri.conf.json` identifier and product metadata.
3. Configure signing and notarization in CI (platform-specific secrets).
4. Verify CSP connect sources only include approved hosts.

## Runtime Parity Requirements

1. Keep request schema parity between desktop and mobile fixture files.
2. Keep response fixtures updated for every contract version.
3. Run `npm run contract:check` whenever contract code or fixtures change.
