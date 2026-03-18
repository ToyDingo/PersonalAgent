import { useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import {
  analyzeUploadedDocument,
  getGoogleAuthStatus,
  sendAgentMessage,
  startGoogleReauthorization,
  uploadDocument,
} from './api'
import type { AgentRequestContext, AgentResponse, CalendarEvent, UploadStatus } from './types'
import { InputPanel } from './components/InputPanel'
import { StatusTimeline } from './components/StatusTimeline'
import { ConfirmationPanel } from './components/ConfirmationPanel'
import { EventsTable } from './components/EventsTable'
import { AdminDebugPanel } from './components/AdminDebugPanel'
import { ServiceReauthPanel } from './components/ServiceReauthPanel'
import { DocumentUploadPanel } from './components/DocumentUploadPanel'
import { DocumentAnalysisPanel } from './components/DocumentAnalysisPanel'
import { useOnlineStatus } from './hooks/useOnlineStatus'

function detectRequestKind(text: string): 'edit' | 'delete' | 'add' | 'retrieve' {
  const normalized = text.toLowerCase()
  if (/\b(edit|update|change|rename|reschedule|move)\b/.test(normalized)) {
    return 'edit'
  }
  if (/\b(delete|remove|cancel)\b/.test(normalized)) {
    return 'delete'
  }
  if (/\b(add|create|insert)\b/.test(normalized)) {
    return 'add'
  }
  return 'retrieve'
}

function phaseForResultAction(action: AgentResponse['action']): string {
  if (action === 'edit_pending_confirmation') {
    return 'Edit candidates found. Review and confirm changes.'
  }
  if (action === 'delete_pending_confirmation') {
    return 'Delete candidates found. Review and confirm removal.'
  }
  if (action === 'add_pending_confirmation') {
    return 'Add candidates found. Review and confirm creation.'
  }
  if (action === 'document_pending_confirmation') {
    return 'Document analysis complete. Review and confirm selected operations.'
  }
  if (action === 'edit') {
    return 'Edit completed.'
  }
  if (action === 'delete') {
    return 'Delete completed.'
  }
  if (action === 'create') {
    return 'Create completed.'
  }
  if (action === 'retrieve') {
    return 'Search completed.'
  }
  if (action === 'reauthorization_required') {
    return 'Authorization required before continuing.'
  }
  if (action === 'document_cancelled') {
    return 'Document operation cancelled.'
  }
  return 'Completed'
}

type ReauthDetails = {
  service: string
  serviceDisplayName: string
  message: string
  resumeContext: { message: string; context: AgentRequestContext } | null
}

function extractReauthDetails(response: AgentResponse | null): ReauthDetails | null {
  if (!response) {
    return null
  }
  const summary = response.summary as Record<string, unknown>
  const requiresReauth = Boolean(summary.requires_reauth) || response.action === 'reauthorization_required'
  if (!requiresReauth) {
    return null
  }
  const resumeRaw = summary.resume_context
  const resumeContext =
    resumeRaw && typeof resumeRaw === 'object'
      ? {
          message:
            typeof (resumeRaw as Record<string, unknown>).message === 'string'
              ? ((resumeRaw as Record<string, unknown>).message as string)
              : '',
          context:
            typeof (resumeRaw as Record<string, unknown>).context === 'object' &&
            (resumeRaw as Record<string, unknown>).context !== null &&
            !Array.isArray((resumeRaw as Record<string, unknown>).context)
              ? ((resumeRaw as Record<string, unknown>).context as AgentRequestContext)
              : {},
        }
      : null
  return {
    service: typeof summary.service === 'string' ? summary.service : 'google_calendar',
    serviceDisplayName:
      typeof summary.service_display_name === 'string'
        ? summary.service_display_name
        : 'Google Calendar',
    message:
      typeof summary.message === 'string'
        ? summary.message
        : 'Service authorization is required to continue.',
    resumeContext:
      resumeContext && resumeContext.message.trim()
        ? resumeContext
        : null,
  }
}

function App() {
  const [message, setMessage] = useState('')
  const [isPublicEvent, setIsPublicEvent] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AgentResponse | null>(null)
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([])
  const [requestDurationMs, setRequestDurationMs] = useState<number | null>(null)
  const [phaseLabel, setPhaseLabel] = useState('Idle')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploadMessage, setUploadMessage] = useState<string>('')
  const [uploadStatus, setUploadStatus] = useState<UploadStatus | 'idle' | 'uploading'>('idle')
  const [activeUploadId, setActiveUploadId] = useState<string | null>(null)
  const [activeUploadFilename, setActiveUploadFilename] = useState<string | null>(null)
  const [documentOperationCounts, setDocumentOperationCounts] = useState<Record<string, number>>({})
  const [documentWarnings, setDocumentWarnings] = useState<string[]>([])
  const activeControllerRef = useRef<AbortController | null>(null)
  const lastRequestRef = useRef<{ message: string; context: AgentRequestContext } | null>(null)
  const isOnline = useOnlineStatus()

  const eventCount = useMemo(() => result?.events.length ?? 0, [result])
  const isPendingConfirmation = Boolean(
    result &&
      (result.action === 'delete_pending_confirmation' ||
        result.action === 'add_pending_confirmation' ||
        result.action === 'edit_pending_confirmation' ||
        result.action === 'document_pending_confirmation')
  )
  const reauthDetails = useMemo(() => extractReauthDetails(result), [result])

  async function runAgentRequest(
    requestMessage: string,
    requestContext: AgentRequestContext,
    controller: AbortController,
    requestKind: 'edit' | 'delete' | 'add' | 'retrieve'
  ): Promise<AgentResponse> {
    lastRequestRef.current = { message: requestMessage, context: requestContext }
    return sendAgentMessage(requestMessage, requestContext, {
      signal: controller.signal,
      onAttemptChange: (attempt, maxRetries) => {
        setPhaseLabel(
          attempt > 1
            ? `Retrying request (${attempt - 1}/${maxRetries})`
            : requestKind === 'edit'
              ? 'Finding events to edit'
              : requestKind === 'delete'
                ? 'Finding events to delete'
                : requestKind === 'add'
                  ? 'Preparing events to create'
                  : 'Awaiting server response'
        )
      },
    })
  }

  async function uploadAndAnalyzeDocument() {
    if (!selectedFile || !uploadMessage.trim()) {
      return
    }
    activeControllerRef.current?.abort()
    setLoading(true)
    setError(null)
    setPhaseLabel('Uploading document')
    try {
      setUploadStatus('uploading')
      const uploaded = await uploadDocument(selectedFile)
      setActiveUploadId(uploaded.upload_id)
      setActiveUploadFilename(uploaded.filename)
      setUploadStatus(uploaded.status)

      setPhaseLabel('AI is reading your document...')
      setUploadStatus('analyzing')
      const response = await analyzeUploadedDocument(uploaded.upload_id, {
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
        message: uploadMessage.trim(),
      })
      setResult(response)
      const summary = response.summary as Record<string, unknown>
      setDocumentOperationCounts(
        typeof summary.operation_counts === 'object' && summary.operation_counts !== null
          ? (summary.operation_counts as Record<string, number>)
          : {}
      )
      setDocumentWarnings(
        Array.isArray(summary.warnings)
          ? summary.warnings.filter((item): item is string => typeof item === 'string')
          : []
      )
      if (
        response.action === 'document_pending_confirmation' ||
        response.action === 'delete_pending_confirmation' ||
        response.action === 'add_pending_confirmation' ||
        response.action === 'edit_pending_confirmation'
      ) {
        const candidates = Array.isArray(response.summary.candidates)
          ? (response.summary.candidates as CalendarEvent[])
          : []
        setSelectedCandidateIds(
          candidates.map((item) => item.id).filter((id): id is string => Boolean(id))
        )
      } else {
        setSelectedCandidateIds([])
      }
      setUploadStatus('analyzed')
      setPhaseLabel(phaseForResultAction(response.action))
    } catch (err) {
      setUploadStatus('error')
      setError(err instanceof Error ? err.message : 'Unknown error')
      setPhaseLabel('Document upload failed')
    } finally {
      setLoading(false)
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!message.trim()) {
      return
    }

    activeControllerRef.current?.abort()
    const controller = new AbortController()
    activeControllerRef.current = controller
    const start = performance.now()
    setLoading(true)
    const requestKind = detectRequestKind(message)
    setPhaseLabel(
      requestKind === 'edit'
        ? 'Starting edit request'
        : requestKind === 'delete'
          ? 'Starting delete request'
          : requestKind === 'add'
            ? 'Starting create request'
            : 'Submitting request'
    )
    setError(null)
    try {
      setPhaseLabel(
        requestKind === 'edit'
          ? 'Finding events to edit'
          : requestKind === 'delete'
            ? 'Finding events to delete'
            : requestKind === 'add'
              ? 'Preparing events to create'
              : 'Awaiting server response'
      )
      const requestMessage = message.trim()
      const requestContext: AgentRequestContext = {
        event_visibility: isPublicEvent ? 'public' : 'private',
      }
      const response = await runAgentRequest(
        requestMessage,
        requestContext,
        controller,
        requestKind
      )
      setResult(response)
      setRequestDurationMs(performance.now() - start)
      if (response.action === 'reauthorization_required') {
        setPhaseLabel('Authorization required before continuing.')
        setSelectedCandidateIds([])
      } else if (
        response.action === 'delete_pending_confirmation' ||
        response.action === 'add_pending_confirmation' ||
        response.action === 'edit_pending_confirmation'
      ) {
        const candidates = Array.isArray(response.summary.candidates)
          ? (response.summary.candidates as CalendarEvent[])
          : []
        setSelectedCandidateIds(
          candidates.map((item) => item.id).filter((id): id is string => Boolean(id))
        )
        setPhaseLabel(phaseForResultAction(response.action))
      } else {
        setSelectedCandidateIds([])
        setPhaseLabel(phaseForResultAction(response.action))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
      setPhaseLabel('Request failed')
    } finally {
      setLoading(false)
      if (activeControllerRef.current === controller) {
        activeControllerRef.current = null
      }
    }
  }

  async function confirmOperation(confirm: boolean) {
    if (
      !result ||
      (result.action !== 'delete_pending_confirmation' &&
        result.action !== 'add_pending_confirmation' &&
        result.action !== 'edit_pending_confirmation' &&
        result.action !== 'document_pending_confirmation')
    ) {
      return
    }
    const confirmationId = String(result.summary.confirmation_id ?? '')
    if (!confirmationId) {
      setError('Missing confirmation_id in response.')
      return
    }
    activeControllerRef.current?.abort()
    const controller = new AbortController()
    activeControllerRef.current = controller
    const start = performance.now()
    setLoading(true)
    const operationType =
      result.action === 'edit_pending_confirmation'
        ? 'edit'
        : result.action === 'delete_pending_confirmation'
          ? 'delete'
          : result.action === 'document_pending_confirmation'
            ? 'document'
            : 'add'
    setPhaseLabel(
      confirm
        ? operationType === 'edit'
          ? 'Editing events in progress'
          : operationType === 'delete'
            ? 'Deleting events in progress'
            : operationType === 'document'
              ? 'Applying document operations'
            : 'Creating events in progress'
        : 'Cancelling operation'
    )
    setError(null)
    try {
      const response = await sendAgentMessage(
        'operation_confirmation',
        {
          operation_confirmation: {
            action: confirm ? 'confirm' : 'cancel',
            confirmation_id: confirmationId,
            selected_event_ids: confirm ? selectedCandidateIds : [],
          },
        },
        {
          signal: controller.signal,
          onAttemptChange: (attempt, maxRetries) => {
            setPhaseLabel(
              attempt > 1
                ? `Retrying confirmation (${attempt - 1}/${maxRetries})`
                : confirm
                  ? operationType === 'edit'
                    ? 'Submitting edit confirmation'
                    : operationType === 'delete'
                      ? 'Submitting delete confirmation'
                      : operationType === 'document'
                        ? 'Submitting document confirmation'
                      : 'Submitting create confirmation'
                  : 'Submitting cancel'
            )
          },
        }
      )
      lastRequestRef.current = {
        message: 'operation_confirmation',
        context: {
          operation_confirmation: {
            action: confirm ? 'confirm' : 'cancel',
            confirmation_id: confirmationId,
            selected_event_ids: confirm ? selectedCandidateIds : [],
          },
        },
      }
      setResult(response)
      setRequestDurationMs(performance.now() - start)
      setSelectedCandidateIds([])
      if (response.action === 'reauthorization_required') {
        setPhaseLabel('Authorization required before continuing.')
      } else {
        setPhaseLabel(
          confirm
            ? operationType === 'edit'
              ? 'Editing completed'
              : operationType === 'delete'
                ? 'Delete completed'
                : operationType === 'document'
                  ? 'Document operations completed'
                : 'Create completed'
            : 'Operation cancelled'
        )
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
      setPhaseLabel('Request failed')
    } finally {
      setLoading(false)
      if (activeControllerRef.current === controller) {
        activeControllerRef.current = null
      }
    }
  }

  function cancelActiveRequest() {
    activeControllerRef.current?.abort()
    setPhaseLabel('Cancelled')
  }

  function toggleCandidate(id: string, checked: boolean) {
    setSelectedCandidateIds((prev) => {
      if (checked) {
        return prev.includes(id) ? prev : [...prev, id]
      }
      return prev.filter((item) => item !== id)
    })
  }

  const pendingCandidates = useMemo(() => {
    if (!result) {
      return []
    }
    return Array.isArray(result.summary.candidates)
      ? (result.summary.candidates as CalendarEvent[])
      : []
  }, [result])

  const confirmationOperation = useMemo<'Add' | 'Delete' | 'Edit' | 'Document' | null>(() => {
    if (!result) {
      return null
    }
    if (result.action === 'delete_pending_confirmation') {
      return 'Delete'
    }
    if (result.action === 'edit_pending_confirmation') {
      return 'Edit'
    }
    if (result.action === 'add_pending_confirmation') {
      return 'Add'
    }
    if (result.action === 'document_pending_confirmation') {
      return 'Document'
    }
    return null
  }, [result])

  async function reauthorizeAndResume() {
    const fallback = lastRequestRef.current
    const resumeContext = reauthDetails?.resumeContext ?? fallback
    if (!resumeContext || !resumeContext.message) {
      setError('No resumable request was found for re-authorization.')
      return
    }
    activeControllerRef.current?.abort()
    const controller = new AbortController()
    activeControllerRef.current = controller
    const start = performance.now()
    setLoading(true)
    setError(null)
    setPhaseLabel(`Starting ${reauthDetails?.serviceDisplayName ?? 'service'} re-authorization`)
    try {
      await startGoogleReauthorization()
      setPhaseLabel('Verifying authorization')
      const status = await getGoogleAuthStatus()
      if (!status.authorized) {
        throw new Error(`Authorization is still unavailable (${status.reason ?? 'unknown_reason'}).`)
      }
      setPhaseLabel('Authorization complete, resuming previous request')
      const resumedKind = detectRequestKind(resumeContext.message)
      const resumedResponse = await runAgentRequest(
        resumeContext.message,
        resumeContext.context,
        controller,
        resumedKind
      )
      setResult(resumedResponse)
      setRequestDurationMs(performance.now() - start)
      setSelectedCandidateIds([])
      if (
        resumedResponse.action === 'delete_pending_confirmation' ||
        resumedResponse.action === 'add_pending_confirmation' ||
        resumedResponse.action === 'edit_pending_confirmation'
      ) {
        const candidates = Array.isArray(resumedResponse.summary.candidates)
          ? (resumedResponse.summary.candidates as CalendarEvent[])
          : []
        setSelectedCandidateIds(
          candidates.map((item) => item.id).filter((id): id is string => Boolean(id))
        )
      }
      setPhaseLabel(phaseForResultAction(resumedResponse.action))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
      setPhaseLabel('Re-authorization failed')
    } finally {
      setLoading(false)
      if (activeControllerRef.current === controller) {
        activeControllerRef.current = null
      }
    }
  }

  function declineReauthorization() {
    setPhaseLabel('Re-authorization declined. Process ended.')
    setSelectedCandidateIds([])
  }

  return (
    <main className="desktop-shell">
      <aside className="agent-sidebar">
        <div className="agent-brand">
          <span className="brand-dot" />
          <div>
            <h1>Personal Agent</h1>
            <p>Calendar command center</p>
          </div>
        </div>
        <button
          type="button"
          className="sidebar-primary-button"
          onClick={() => {
            setMessage('')
            setError(null)
            setResult(null)
            setSelectedCandidateIds([])
            setPhaseLabel('Idle')
            setSelectedFile(null)
            setUploadMessage('')
            setUploadStatus('idle')
            setActiveUploadId(null)
            setActiveUploadFilename(null)
            setDocumentOperationCounts({})
            setDocumentWarnings([])
          }}
        >
          New request
        </button>
        <div className="sidebar-section">
          <p className="sidebar-label">Runtime</p>
          <p className="sidebar-value">{isOnline ? 'Online' : 'Offline'}</p>
          <p className="sidebar-value">{loading ? 'Processing' : 'Ready'}</p>
        </div>
        <div className="sidebar-section">
          <p className="sidebar-label">Last action</p>
          <p className="sidebar-value">{result?.action ?? 'none'}</p>
        </div>
      </aside>

      <section className="workspace-area">
        <header className="workspace-header">
          <h2>Assistant Workspace</h2>
          <p>Styled prototype merged into your existing production flow.</p>
        </header>

        <div className="layout-grid">
          <div className="column-left">
            <InputPanel
              message={message}
              isPublicEvent={isPublicEvent}
              isOnline={isOnline}
              loading={loading}
              onMessageChange={setMessage}
              onPublicEventChange={setIsPublicEvent}
              onSubmit={onSubmit}
              onCancel={cancelActiveRequest}
            />
            <StatusTimeline
              loading={loading}
              hasResult={Boolean(result)}
              isPendingConfirmation={isPendingConfirmation}
              latestAction={result?.action ?? null}
              phaseLabel={phaseLabel}
              isOnline={isOnline}
            />
            <DocumentUploadPanel
              selectedFile={selectedFile}
              message={uploadMessage}
              uploadStatus={uploadStatus}
              loading={loading}
              onFileChange={setSelectedFile}
              onMessageChange={setUploadMessage}
              onAnalyze={uploadAndAnalyzeDocument}
            />
            <DocumentAnalysisPanel
              uploadId={activeUploadId}
              filename={activeUploadFilename}
              operationCounts={documentOperationCounts}
              warnings={documentWarnings}
            />
            {error && (
              <section className="panel error">
                <h2>Error</h2>
                <pre>{error}</pre>
              </section>
            )}
          </div>

          <div className="column-right">
            {result && reauthDetails && (
              <ServiceReauthPanel
                serviceDisplayName={reauthDetails.serviceDisplayName}
                message={reauthDetails.message}
                loading={loading}
                onAccept={reauthorizeAndResume}
                onDecline={declineReauthorization}
              />
            )}
            {result && !reauthDetails && confirmationOperation && (
              <ConfirmationPanel
                operationLabel={confirmationOperation}
                candidates={pendingCandidates}
                selectedCandidateIds={selectedCandidateIds}
                loading={loading}
                onToggle={toggleCandidate}
                onConfirm={() => confirmOperation(true)}
                onCancel={() => confirmOperation(false)}
              />
            )}
            {result && !reauthDetails && (
              <EventsTable title={`Events (${eventCount})`} events={result.events} />
            )}
          </div>
        </div>
      </section>
      <AdminDebugPanel response={result} requestDurationMs={requestDurationMs} />
    </main>
  )
}

export default App
