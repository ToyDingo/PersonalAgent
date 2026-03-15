import type { AgentResponse } from '../types'

type ActionSummaryCardProps = {
  response: AgentResponse
}

export function ActionSummaryCard({ response }: ActionSummaryCardProps) {
  const summary = response.summary

  return (
    <section className="panel">
      <h2>Operation Summary</h2>
      <div className="kv-grid">
        <div>
          <span className="kv-key">action</span>
          <span className="kv-value">{response.action}</span>
        </div>
        <div>
          <span className="kv-key">calendar_id</span>
          <span className="kv-value">{String(summary.calendar_id ?? response.meta.default_calendar_id)}</span>
        </div>
        <div>
          <span className="kv-key">events_count</span>
          <span className="kv-value">
            {String(
              summary.events_created_count ??
                summary.events_found_count ??
                summary.candidate_count ??
                response.events.length
            )}
          </span>
        </div>
        <div>
          <span className="kv-key">timestamp_local</span>
          <span className="kv-value">{response.meta.current_datetime_local}</span>
        </div>
      </div>
      {typeof summary.error === 'string' && (
        <p className="summary-error">error: {summary.error}</p>
      )}
      {typeof summary.message === 'string' && (
        <p className="summary-note">{summary.message}</p>
      )}
    </section>
  )
}

