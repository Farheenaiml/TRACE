const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

export type Repository = { id: string; name: string; description: string; language: string; decisions: number; commit_count?: number }
export type Health = {
  repoId: string
  health_status?: string
  health_score?: number
  health_confidence?: string
  open_issue_count: number
  open_pr_count?: number
  backlog_growth_rate?: number
  pr_backlog_growth_rate?: number
  duplicate_rate?: number
  active_contributor_count?: number
  security_flag_count?: number
  avg_response_time_hours?: number
  response_time_growth_rate?: number
  projected_backlog_next_week?: number | null
  projected_contributor_trend?: string | null
  forecast_status?: string
  health_reasons?: Array<{ metric?: string; message?: string; impact?: number }>
  health_dashboard?: Record<string, { value?: number; value_hours?: number; value_percent?: number; change_percent?: number }>
  history?: Array<{ timestamp: string; open_issue_count: number }>
}
export type Investigation = {
  issue_id?: string; title?: string; url?: string; decision?: string; reason?: string
  security_sensitive?: boolean; duplicates?: Array<{ title?: string; similarity?: number; url?: string }>
  importance?: { score?: number; reasons?: string[] }
  explanation?: { confidence_percent?: number; reasons?: string[]; evidence?: Array<{ type?: string; label?: string; detail?: string; url?: string }> }
  follow_up?: { needs_follow_up?: boolean; missing?: string[]; message?: string }
  investigation_timeline?: Array<{ step?: string; label?: string; started_at?: string; evidence?: Record<string, unknown> }>
}
export type InboxItem = Investigation
export type MonitorRun = { repoId?: string; status?: string; steps?: Array<{ step: string; status: string; detail?: string }>; results?: { investigations?: Investigation[]; health?: Health; brief?: Brief } }
export type Brief = { generated_at?: string; summary_text?: string; raw_stats?: { open_issue_count_this_week?: number; open_issue_count_last_week?: number; decision_counts?: Record<string, number>; top_discussed_issues?: Array<{ title?: string; comments?: number; url?: string }> } }

export async function fetchBackend<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { 'Content-Type': 'application/json', ...(options?.headers ?? {}) }, ...options })
  if (!response.ok) {
    let message = `Backend request failed: ${response.status}`
    try { message = (await response.json()).detail ?? message } catch { /* use status */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const traceApi = {
  repository: (repoId: string) => fetchBackend<Repository>(`/repos/${encodeURIComponent(repoId)}`),
  ingest: (repoUrl: string) => fetchBackend<Repository>('/repos/ingest', { method: 'POST', body: JSON.stringify({ repoUrl }) }),
  ingestStatus: (repoId: string) => fetchBackend<{ status: string }>(`/repos/ingest/status/${encodeURIComponent(repoId)}`),
  health: (repoId: string) => fetchBackend<Health>(`/health?repoId=${encodeURIComponent(repoId)}`),
  inbox: (repoId: string) => fetchBackend<{ repoId: string; status: string; count: number; items: InboxItem[] }>(`/repos/inbox?repoId=${encodeURIComponent(repoId)}`),
  investigations: (repoId: string) => fetchBackend<{ repoId: string; status: string; steps: MonitorRun['steps']; investigations: Investigation[] }>(`/repos/investigations?repoId=${encodeURIComponent(repoId)}`),
  monitor: (repoId: string) => fetchBackend<MonitorRun>(`/repos/monitor/status?repoId=${encodeURIComponent(repoId)}`),
  brief: (repoId: string) => fetchBackend<Brief>(`/brief?repoId=${encodeURIComponent(repoId)}`),
}

export function normalizeRepoId(value: string) {
  const input = value.trim().replace(/\/$/, '')
  const github = input.match(/github\.com[/:]([^/]+)\/([^/]+?)(?:\.git)?$/i)
  if (github) return `${github[1]}/${github[2]}`
  return input.replace(/^https?:\/\//i, '').split('/').slice(0, 2).join('/')
}
