import type { CalendarEvent } from '../types'

type EventsTableProps = {
  title: string
  events: CalendarEvent[]
}

export function EventsTable({ title, events }: EventsTableProps) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {events.length === 0 ? (
        <p>No events in this response.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Summary</th>
                <th>Start</th>
                <th>End</th>
                <th>Timezone</th>
                <th>Calendar</th>
              </tr>
            </thead>
            <tbody>
              {events.map((item) => (
                <tr key={`${item.id}-${item.start_iso}`}>
                  <td>{item.summary ?? '(no title)'}</td>
                  <td>{item.start_iso ?? '-'}</td>
                  <td>{item.end_iso ?? '-'}</td>
                  <td>{item.timezone ?? '-'}</td>
                  <td>{item.source_calendar}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

