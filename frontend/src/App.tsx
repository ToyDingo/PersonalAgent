import { useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { sendAgentMessage } from './api'
import type { AgentResponse, CalendarEvent } from './types'
import { InputPanel } from './components/InputPanel'
import { StatusTimeline } from './components/StatusTimeline'
import { ConfirmationPanel } from './components/ConfirmationPanel'
import { EventsTable } from './components/EventsTable'
import { AdminDebugPanel } from './components/AdminDebugPanel'
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
  return 'Completed'
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
  const activeControllerRef = useRef<AbortController | null>(null)
  const isOnline = useOnlineStatus()

  const eventCount = useMemo(() => result?.events.length ?? 0, [result])
  const isPendingConfirmation = Boolean(
    result &&
      (result.action === 'delete_pending_confirmation' ||
        result.action === 'add_pending_confirmation' ||
        result.action === 'edit_pending_confirmation')
  )

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
      const response = await sendAgentMessage(
        message.trim(),
        {
          event_visibility: isPublicEvent ? 'public' : 'private',
        },
        {
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
        }
      )
      setResult(response)
      setRequestDurationMs(performance.now() - start)
      if (
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
        result.action !== 'edit_pending_confirmation')
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
          : 'add'
    setPhaseLabel(
      confirm
        ? operationType === 'edit'
          ? 'Editing events in progress'
          : operationType === 'delete'
            ? 'Deleting events in progress'
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
                      : 'Submitting create confirmation'
                  : 'Submitting cancel'
            )
          },
        }
      )
      setResult(response)
      setRequestDurationMs(performance.now() - start)
      setSelectedCandidateIds([])
      setPhaseLabel(
        confirm
          ? operationType === 'edit'
            ? 'Editing completed'
            : operationType === 'delete'
              ? 'Delete completed'
              : 'Create completed'
          : 'Operation cancelled'
      )
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

  const confirmationOperation = useMemo<'Add' | 'Delete' | 'Edit' | null>(() => {
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
    return null
  }, [result])

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
            {error && (
              <section className="panel error">
                <h2>Error</h2>
                <pre>{error}</pre>
              </section>
            )}
          </div>

          <div className="column-right">
            {result && confirmationOperation && (
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
            {result && (
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
