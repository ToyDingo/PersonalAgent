import type { CalendarEvent } from '../types'

type ConfirmationPanelProps = {
  operationLabel: 'Add' | 'Delete' | 'Edit' | 'Document'
  candidates: CalendarEvent[]
  selectedCandidateIds: string[]
  loading: boolean
  onToggle: (id: string, checked: boolean) => void
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmationPanel({
  operationLabel,
  candidates,
  selectedCandidateIds,
  loading,
  onToggle,
  onConfirm,
  onCancel,
}: ConfirmationPanelProps) {
  return (
    <section className="panel">
      <h2>Confirm {operationLabel}</h2>
      <p className="summary-note">
        Selected {selectedCandidateIds.length} of {candidates.length}.
      </p>
      {candidates.length === 0 ? (
        <p>No candidates available.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Select</th>
                <th>Summary</th>
                <th>Start</th>
                <th>End</th>
                <th>Timezone</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((item) => {
                const id = item.id ?? ''
                return (
                  <tr key={`${id}-${item.start_iso}`}>
                    <td>
                      <input
                        type="checkbox"
                        checked={id ? selectedCandidateIds.includes(id) : false}
                        onChange={(event) => id && onToggle(id, event.target.checked)}
                        disabled={!id || loading}
                      />
                    </td>
                    <td>{item.summary ?? '(no title)'}</td>
                    <td>{item.start_iso ?? '-'}</td>
                    <td>{item.end_iso ?? '-'}</td>
                    <td>{item.timezone ?? '-'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="actions-row">
        <button type="button" onClick={onConfirm} disabled={loading}>
          Yes, confirm selected
        </button>
        <button type="button" className="button-danger" onClick={onCancel} disabled={loading}>
          No, cancel
        </button>
      </div>
    </section>
  )
}

