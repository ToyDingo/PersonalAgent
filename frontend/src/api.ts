import { buildAgentRequest, parseAgentResponse } from './contracts/agentContract'
import type { AgentRequestContext, AgentResponse } from './types'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

const DEFAULT_TIMEOUT_MS = 20_000
const MAX_RETRIES = 1

function isRetryableStatus(status: number): boolean {
  return status === 408 || status === 429 || status >= 500
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

export type SendAgentMessageOptions = {
  signal?: AbortSignal
  timeoutMs?: number
  onAttemptChange?: (attempt: number, maxRetries: number) => void
}

export async function sendAgentMessage(
  message: string,
  context?: AgentRequestContext,
  options?: SendAgentMessageOptions
): Promise<AgentResponse> {
  const timeoutMs = options?.timeoutMs ?? DEFAULT_TIMEOUT_MS

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
    options?.onAttemptChange?.(attempt + 1, MAX_RETRIES)
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
    const onAbort = () => controller.abort()
    options?.signal?.addEventListener('abort', onAbort, { once: true })

    try {
      const response = await fetch(`${API_BASE_URL}/agent/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(buildAgentRequest(message, context)),
        signal: controller.signal,
      })

      if (!response.ok) {
        const body = await response.text()
        if (attempt < MAX_RETRIES && isRetryableStatus(response.status)) {
          await wait(400 * (attempt + 1))
          continue
        }
        throw new Error(`Backend error (${response.status}): ${body}`)
      }

      const json = await response.json()
      return parseAgentResponse(json)
    } catch (error) {
      if (controller.signal.aborted) {
        if (options?.signal?.aborted) {
          throw new Error('Request cancelled.')
        }
        throw new Error('Request timed out. Please try again.')
      }
      if (attempt < MAX_RETRIES) {
        await wait(400 * (attempt + 1))
        continue
      }
      throw error
    } finally {
      window.clearTimeout(timeout)
      options?.signal?.removeEventListener('abort', onAbort)
    }
  }

  throw new Error('Request failed after retries.')
}

