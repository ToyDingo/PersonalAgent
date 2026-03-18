export type CalendarEvent = {
  id: string | null
  summary: string | null
  description: string | null
  location: string | null
  status: string | null
  html_link: string | null
  start_iso: string | null
  end_iso: string | null
  timezone: string | null
  is_all_day: boolean
  source_calendar: string
  reminders?: {
    useDefault?: boolean
    overrides?: Array<{
      method: 'popup' | 'email'
      minutes: number
    }>
  }
  visibility?: string
  color_id?: string
  event_type?: string
}

export type AgentAction =
  | 'create'
  | 'edit'
  | 'edit_pending_confirmation'
  | 'edit_cancelled'
  | 'document_pending_confirmation'
  | 'document_cancelled'
  | 'add_pending_confirmation'
  | 'add_cancelled'
  | 'retrieve'
  | 'reauthorization_required'
  | 'delete'
  | 'delete_pending_confirmation'
  | 'delete_cancelled'
  | 'mixed'
  | 'none'

export type AgentResponse = {
  result_type: 'calendar_events'
  action: AgentAction
  summary: Record<string, unknown>
  events: CalendarEvent[]
  meta: {
    default_calendar_id: string
    current_datetime_utc: string
    current_datetime_local: string
    query: string
    web_search_mode?: 'public' | 'private' | 'auto'
    resolved_time_window?: {
      source_phrase: string
      start_iso: string
      end_iso: string
      timezone: string
    } | null
  }
  tool_results: ToolResult[]
}

export type ToolResult = {
  id?: string
  name?: string
  arguments?: Record<string, unknown>
  result?: Record<string, unknown>
}

export type AgentRequestContext = Record<string, unknown>

export type AgentRequestPayload = {
  message: string
  context: AgentRequestContext
}

export type UploadStatus = 'uploaded' | 'analyzing' | 'analyzed' | 'error'

export type UploadCreateResponse = {
  upload_id: string
  status: UploadStatus
  filename: string
  content_type: string
  extension: string
  size_bytes: number
  created_at_utc: string
}

export type UploadRecord = {
  upload_id: string
  status: UploadStatus
  filename: string
  content_type: string
  extension: string
  size_bytes: number
  error_code?: string
  error_message?: string
  created_at_utc: string
  updated_at_utc: string
}

