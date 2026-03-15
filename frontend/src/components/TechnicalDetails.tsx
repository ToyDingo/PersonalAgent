import type { AgentResponse } from '../types'

type TechnicalDetailsProps = {
  response: AgentResponse
  requestDurationMs: number | null
}

export function TechnicalDetails({ response, requestDurationMs }: TechnicalDetailsProps) {
  const toolLines = response.tool_results.map((item) => {
    const name = item.name ?? 'unknown_tool'
    const result = item.result ?? {}
    const eventsCount =
      typeof result.events_count === 'number' || typeof result.events_count === 'string'
        ? result.events_count
        : 'n/a'
    const source = typeof result.source === 'string' ? result.source : 'n/a'
    const performance =
      result.performance && typeof result.performance === 'object'
        ? (result.performance as Record<string, unknown>)
        : null
    const elapsed =
      performance && typeof performance.total_elapsed_ms === 'number'
        ? performance.total_elapsed_ms
        : 'n/a'
    return `${name} | source=${String(source ?? 'n/a')} | events=${String(
      eventsCount ?? 'n/a'
    )} | elapsed_ms=${String(elapsed ?? 'n/a')}`
  })

  return (
    <section className="panel">
      <details>
        <summary>Technical details</summary>
        <div className="technical-details">
          <p>
            <strong>request_duration_ms:</strong>{' '}
            {requestDurationMs === null ? 'n/a' : requestDurationMs.toFixed(1)}
          </p>
          <p>
            <strong>tool_calls:</strong> {response.tool_results.length}
          </p>
          <p>
            <strong>query:</strong> {response.meta.query}
          </p>
          <p>
            <strong>web_search_mode:</strong> {response.meta.web_search_mode ?? 'auto'}
          </p>
          <div>
            <strong>tool_diagnostics:</strong>
            {toolLines.length === 0 ? (
              <p>none</p>
            ) : (
              <ul className="tool-list">
                {toolLines.map((line, idx) => (
                  <li key={`${idx}-${line}`}>{line}</li>
                ))}
              </ul>
            )}
          </div>
          <pre>{JSON.stringify(response, null, 2)}</pre>
        </div>
      </details>
    </section>
  )
}

