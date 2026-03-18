# Prompt Validation Baseline Report

- Generated at: 2026-03-18T21:37:32.955Z
- API base URL: http://127.0.0.1:8000
- Suite version: v1.2
- Overall pass (required cases): yes
- Required failures: 0
- Optional failures: 0
- Latency ms (min/p50/p95/max): 398/682/41163/63063

## Upload Probe

- attempted: yes
- passed: yes
- elapsed: 1935ms
- note: upload_id=da5a79a7-9a22-40d9-8bc4-84680e497c33

## Case Results

- [PASS] retrieve-named-this-year (required) action=retrieve elapsed=758ms tools=[search_calendar_events]
- [PASS] retrieve-this-morning (required) action=retrieve elapsed=586ms tools=[search_calendar_events]
- [PASS] retrieve-this-week-future-default (required) action=retrieve elapsed=532ms tools=[search_calendar_events]
- [PASS] retrieve-this-week-so-far (required) action=retrieve elapsed=416ms tools=[search_calendar_events]
- [PASS] retrieve-generic-this-month (required) action=retrieve elapsed=398ms tools=[search_calendar_events]
- [PASS] retrieve-last-month-explicit-past (required) action=retrieve elapsed=533ms tools=[search_calendar_events]
- [PASS] edit-bulk-rename-pending (required) action=none elapsed=1204ms tools=[edit_calendar_events]
  - followup(cancel): PASS action=n/a elapsed=n/ams (skipped)
- [PASS] delete-query-pending (required) action=delete_pending_confirmation elapsed=682ms tools=[delete_calendar_events]
  - followup(cancel): PASS action=delete_cancelled elapsed=2ms
- [PASS] sports-disambiguation-atlanta-united (optional) action=retrieve elapsed=41163ms tools=[search_official_sources]
- [PASS] add-with-reminder (required) action=add_pending_confirmation elapsed=5566ms tools=[create_event]
  - followup(cancel): PASS action=add_cancelled elapsed=2ms
- [PASS] edit-targeted-location-update (optional) action=edit_pending_confirmation elapsed=17577ms tools=[search_calendar_events, edit_calendar_events]
- [PASS] add-f1-races-official-source (optional) action=add_pending_confirmation elapsed=63063ms tools=[search_official_sources, batch_create_events, batch_create_events]
  - followup(cancel): PASS action=add_cancelled elapsed=2ms

