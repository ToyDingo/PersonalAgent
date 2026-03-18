import {
  buildAgentRequest,
  buildUploadAnalyzeRequest,
  parseAgentResponse,
  parseUploadResponse,
} from './contracts/agentContract'
import type {
  AgentRequestContext,
  AgentResponse,
  UploadCreateResponse,
  UploadRecord,
} from './types'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

const DEFAULT_TIMEOUT_MS = 60_000
const AUTH_FLOW_TIMEOUT_MS = 300_000
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

export type GoogleAuthStatus = {
  authorized: boolean
  reason?: string
}

export async function getGoogleAuthStatus(): Promise<GoogleAuthStatus> {
  const response = await fetch(`${API_BASE_URL}/auth/google/status`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`Failed to check Google auth status (${response.status}): ${body}`)
  }
  const data = (await response.json()) as GoogleAuthStatus
  return {
    authorized: Boolean(data.authorized),
    reason: typeof data.reason === 'string' ? data.reason : undefined,
  }
}

export async function startGoogleReauthorization(timeoutMs: number = AUTH_FLOW_TIMEOUT_MS): Promise<void> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${API_BASE_URL}/auth/google/start`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      signal: controller.signal,
    })
    if (!response.ok) {
      const body = await response.text()
      throw new Error(`Failed to start Google re-authorization (${response.status}): ${body}`)
    }
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error('Google re-authorization timed out. Please try again.')
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function uploadDocument(file: File): Promise<UploadCreateResponse> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`${API_BASE_URL}/agent/uploads`, {
    method: 'POST',
    body: formData,
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`Upload failed (${response.status}): ${body}`)
  }
  return parseUploadResponse(await response.json()) as UploadCreateResponse
}

export async function getUploadStatus(uploadId: string): Promise<UploadRecord> {
  const response = await fetch(`${API_BASE_URL}/agent/uploads/${encodeURIComponent(uploadId)}`, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`Upload status failed (${response.status}): ${body}`)
  }
  return (await response.json()) as UploadRecord
}

export async function analyzeUploadedDocument(
  uploadId: string,
  options: { timezone?: string; message: string }
): Promise<AgentResponse> {
  const response = await fetch(
    `${API_BASE_URL}/agent/uploads/${encodeURIComponent(uploadId)}/analyze`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(
        buildUploadAnalyzeRequest(uploadId, options.message, options.timezone, {
          upload_source: 'desktop',
        })
      ),
    }
  )
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`Document analysis failed (${response.status}): ${body}`)
  }
  const json = await response.json()
  return parseAgentResponse(json)
}

