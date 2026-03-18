import type { UploadStatus } from '../types'

type DocumentUploadPanelProps = {
  selectedFile: File | null
  message: string
  uploadStatus: UploadStatus | 'idle' | 'uploading'
  loading: boolean
  onFileChange: (file: File | null) => void
  onMessageChange: (value: string) => void
  onAnalyze: () => void
}

export function DocumentUploadPanel({
  selectedFile,
  message,
  uploadStatus,
  loading,
  onFileChange,
  onMessageChange,
  onAnalyze,
}: DocumentUploadPanelProps) {
  return (
    <section className="panel">
      <h2>Document Upload</h2>
      <p className="summary-note">
        Upload a file and tell the AI what to do with it, for example: Add all dates in this
        document to my calendar.
      </p>
      <label className="summary-note" htmlFor="document-upload-message">
        Instruction
      </label>
      <textarea
        id="document-upload-message"
        value={message}
        onChange={(event) => onMessageChange(event.target.value)}
        placeholder="Add all the dates in this document to my calendar"
        disabled={loading}
        rows={3}
      />
      <input
        type="file"
        accept=".txt,.docx,.pdf,.png,.jpg,.jpeg,.xlsx,.ics"
        onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
        disabled={loading}
      />
      <p className="summary-note">
        Selected: {selectedFile ? `${selectedFile.name} (${selectedFile.size} bytes)` : 'none'}
      </p>
      <p className="summary-note">status: {uploadStatus}</p>
      <div className="actions-row">
        <button
          type="button"
          disabled={loading || !selectedFile || !message.trim()}
          onClick={onAnalyze}
        >
          {loading ? 'Processing...' : 'Upload and extract events'}
        </button>
      </div>
    </section>
  )
}

