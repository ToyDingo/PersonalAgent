import type { AgentRequestPayload, AgentResponse } from '../types'

const CONTRACT_VERSION = 'v1'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function buildAgentRequest(
  message: string,
  context?: Record<string, unknown>
): AgentRequestPayload {
  return {
    message,
    context: {
      ...(context ?? {}),
      contract_version: CONTRACT_VERSION,
      client_platform: 'desktop',
    },
  }
}

export function parseAgentResponse(value: unknown): AgentResponse {
  if (!isRecord(value)) {
    throw new Error('Invalid response: expected object payload.')
  }
  if (value.result_type !== 'calendar_events') {
    throw new Error('Invalid response: unsupported result_type.')
  }
  if (typeof value.action !== 'string') {
    throw new Error('Invalid response: missing action.')
  }
  if (!Array.isArray(value.events)) {
    throw new Error('Invalid response: events must be an array.')
  }
  if (!isRecord(value.meta)) {
    throw new Error('Invalid response: missing meta object.')
  }
  if (!Array.isArray(value.tool_results)) {
    throw new Error('Invalid response: tool_results must be an array.')
  }
  return value as AgentResponse
}

