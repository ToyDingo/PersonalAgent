import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ActionSummaryCard } from './ActionSummaryCard'
import type { AgentResponse } from '../types'

const baseResponse: AgentResponse = {
  result_type: 'calendar_events',
  action: 'none',
  summary: {
    calendar_id: 'primary',
    error: 'no_editable_events_found',
  },
  events: [],
  meta: {
    default_calendar_id: 'primary',
    current_datetime_utc: '2026-03-20T12:00:00+00:00',
    current_datetime_local: '2026-03-20T08:00:00-04:00',
    query: 'edit my meeting',
  },
  tool_results: [],
}

describe('ActionSummaryCard', () => {
  it('shows no-match hint when a no-editable-events code is returned', () => {
    render(<ActionSummaryCard response={baseResponse} />)
    expect(screen.getByText('Operation Summary')).toBeInTheDocument()
    expect(screen.getByText(/No matching events were found for this request/)).toBeInTheDocument()
  })
})
