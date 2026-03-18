type DocumentAnalysisPanelProps = {
  uploadId: string | null
  filename: string | null
  operationCounts: Record<string, number>
  warnings: string[]
}

export function DocumentAnalysisPanel({
  uploadId,
  filename,
  operationCounts,
  warnings,
}: DocumentAnalysisPanelProps) {
  if (!uploadId) {
    return null
  }
  return (
    <section className="panel">
      <h2>Document Analysis</h2>
      <p className="summary-note">upload_id: {uploadId}</p>
      <p className="summary-note">filename: {filename ?? '(unknown)'}</p>
      <p className="summary-note">
        operations: add={operationCounts.add ?? 0}, edit={operationCounts.edit ?? 0}, delete=
        {operationCounts.delete ?? 0}
      </p>
      {warnings.length > 0 && (
        <div>
          <p className="summary-error">warnings:</p>
          <ul>
            {warnings.map((warning, index) => (
              <li key={`${index}-${warning}`}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

