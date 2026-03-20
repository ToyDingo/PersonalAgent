import { describe, expect, it } from 'vitest'

import {
  buildAgentRequest,
  buildUploadAnalyzeRequest,
  parseAgentResponse,
  parseUploadResponse,
} from './agentContract'

describe('agentContract', () => {
  it('builds agent requests with v1 context', () => {
    const request = buildAgentRequest('show my schedule', { timezone: 'UTC' })
    expect(request.message).toBe('show my schedule')
    expect(request.context.contract_version).toBe('v1')
    expect(request.context.client_platform).toBe('desktop')
    expect(request.context.timezone).toBe('UTC')
  })

  it('builds upload analyze requests with contract metadata', () => {
    const request = buildUploadAnalyzeRequest('upload-1', 'analyze this', 'UTC', {
      upload_source: 'desktop',
    })
    const context = request.context as Record<string, unknown>
    expect(request.upload_id).toBe('upload-1')
    expect(context.contract_version).toBe('v1')
    expect(context.client_platform).toBe('desktop')
    expect(context.upload_source).toBe('desktop')
  })

  it('parses valid agent responses', () => {
    const parsed = parseAgentResponse({
      result_type: 'calendar_events',
      action: 'retrieve',
      summary: { calendar_id: 'primary' },
      events: [],
      meta: {
        default_calendar_id: 'primary',
        current_datetime_utc: '2026-03-20T00:00:00+00:00',
        current_datetime_local: '2026-03-19T20:00:00-04:00',
        query: 'show my events',
      },
      tool_results: [],
    })
    expect(parsed.action).toBe('retrieve')
  })

  it('throws when response shape is invalid', () => {
    expect(() => parseAgentResponse({ action: 'retrieve' })).toThrow(
      'Invalid response: unsupported result_type.'
    )
  })

  it('parses upload response payloads', () => {
    const parsed = parseUploadResponse({
      upload_id: 'abc',
      status: 'uploaded',
      filename: 'notes.txt',
    })
    expect(parsed.upload_id).toBe('abc')
  })
})
