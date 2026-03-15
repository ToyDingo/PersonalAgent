import type { FormEvent } from 'react'

type InputPanelProps = {
  message: string
  isPublicEvent: boolean
  isOnline: boolean
  loading: boolean
  onMessageChange: (value: string) => void
  onPublicEventChange: (value: boolean) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onCancel: () => void
}

export function InputPanel({
  message,
  isPublicEvent,
  isOnline,
  loading,
  onMessageChange,
  onPublicEventChange,
  onSubmit,
  onCancel,
}: InputPanelProps) {
  return (
    <section className="panel panel-input">
      <div className="panel-header">
        <h2>Calendar Assistant</h2>
        <p>Describe what to add, edit, delete, or find.</p>
        {!isOnline && (
          <p className="summary-error">
            You are offline. Requests requiring internet research will fail until connection is restored.
          </p>
        )}
      </div>
      <form onSubmit={onSubmit}>
        <label htmlFor="message">Message</label>
        <textarea
          id="message"
          value={message}
          onChange={(event) => onMessageChange(event.target.value)}
          placeholder="Add all Atlanta United games to my calendar for 2026."
          rows={5}
          disabled={loading}
        />
        <label className="inline-checkbox">
          <input
            type="checkbox"
            checked={isPublicEvent}
            onChange={(event) => onPublicEventChange(event.target.checked)}
            disabled={loading}
          />
          Allow public internet event search
        </label>
        <div className="actions-row">
          <button type="submit" disabled={loading || !message.trim()}>
            {loading ? 'Running...' : 'Run'}
          </button>
          <button
            type="button"
            className="button-muted"
            onClick={onCancel}
            disabled={!loading}
          >
            Cancel request
          </button>
        </div>
      </form>
    </section>
  )
}

