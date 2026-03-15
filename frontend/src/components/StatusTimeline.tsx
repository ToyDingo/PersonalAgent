type StatusTimelineProps = {
  loading: boolean
  hasResult: boolean
  isPendingConfirmation: boolean
  latestAction: string | null
  phaseLabel: string
  isOnline: boolean
}

export function StatusTimeline({
  loading,
  hasResult,
  isPendingConfirmation,
  latestAction,
  phaseLabel,
  isOnline,
}: StatusTimelineProps) {
  const status = loading
    ? 'Processing'
    : isPendingConfirmation
      ? 'Awaiting confirmation'
      : hasResult
        ? 'Completed'
        : 'Idle'

  return (
    <section className="panel">
      <h2>Status</h2>
      <div className="status-row">
        <span className={`status-pill ${loading ? 'is-busy' : 'is-ready'}`}>{status}</span>
        <span className={`status-pill ${isOnline ? 'is-online' : 'is-offline'}`}>
          {isOnline ? 'Online' : 'Offline'}
        </span>
        <span className="status-detail">phase: {phaseLabel}</span>
        {latestAction && <span className="status-detail">last_action: {latestAction}</span>}
      </div>
    </section>
  )
}

