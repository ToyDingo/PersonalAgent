import { useState } from 'react'
import type { AgentResponse } from '../types'
import { ActionSummaryCard } from './ActionSummaryCard'
import { TechnicalDetails } from './TechnicalDetails'

type AdminDebugPanelProps = {
  response: AgentResponse | null
  requestDurationMs: number | null
}

export function AdminDebugPanel({ response, requestDurationMs }: AdminDebugPanelProps) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        type="button"
        className="admin-fab"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-label="Toggle admin diagnostics"
      >
        Admin
      </button>

      {open && (
        <section className="admin-debug-window" role="dialog" aria-label="Admin diagnostics panel">
          <header className="admin-debug-header">
            <h3>Admin Diagnostics</h3>
            <button type="button" className="button-muted" onClick={() => setOpen(false)}>
              Close
            </button>
          </header>

          <div className="admin-debug-content">
            {response ? (
              <>
                <ActionSummaryCard response={response} />
                <TechnicalDetails
                  response={response}
                  requestDurationMs={requestDurationMs}
                />
              </>
            ) : (
              <div className="panel">
                <h2>Operation Summary</h2>
                <p className="summary-note">Run a request to populate diagnostics.</p>
              </div>
            )}
          </div>
        </section>
      )}
    </>
  )
}

