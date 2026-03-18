type ServiceReauthPanelProps = {
  serviceDisplayName: string
  message: string
  loading: boolean
  onAccept: () => void
  onDecline: () => void
}

export function ServiceReauthPanel({
  serviceDisplayName,
  message,
  loading,
  onAccept,
  onDecline,
}: ServiceReauthPanelProps) {
  return (
    <section className="panel">
      <h2>Re-authorization required</h2>
      <p className="summary-note">
        Lost access to <strong>{serviceDisplayName}</strong>.
      </p>
      <p>{message}</p>
      <div className="actions-row">
        <button type="button" onClick={onAccept} disabled={loading}>
          Re-authorize and continue
        </button>
        <button type="button" className="button-muted" onClick={onDecline} disabled={loading}>
          Not now
        </button>
      </div>
    </section>
  )
}

