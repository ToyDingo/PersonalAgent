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
  const isImageSelection =
    selectedFile !== null &&
    (selectedFile.type.startsWith('image/') ||
      /\.(png|jpg|jpeg)$/i.test(selectedFile.name))

  return (
    <section className="panel">
      <h2>Documents and images</h2>
      <p className="summary-note">
        Upload a document, spreadsheet, or photo (PNG/JPEG) and describe what you want. For
        example: add every date in this file to my calendar, or extract events from this screenshot
        of an invitation.
      </p>
      <label className="summary-note" htmlFor="document-upload-message">
        Instruction
      </label>
      <textarea
        id="document-upload-message"
        value={message}
        onChange={(event) => onMessageChange(event.target.value)}
        placeholder={
          isImageSelection
            ? 'Extract all events from this image and add them to my calendar'
            : 'Add all the dates in this document to my calendar'
        }
        disabled={loading}
        rows={3}
      />
      <input
        type="file"
        accept=".txt,.docx,.pdf,.png,.jpg,.jpeg,.xlsx,.ics"
        onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
        disabled={loading}
      />
      {isImageSelection && (
        <p className="summary-note">
          The AI will read text and dates from this picture (same confirmation step as documents).
        </p>
      )}
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

