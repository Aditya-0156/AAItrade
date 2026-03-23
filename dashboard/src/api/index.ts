import client from './client'
import type {
  Session,
  Trade,
  PortfolioPosition,
  Decision,
  ToolCall,
  JournalEntry,
  ThesisUpdate,
  SessionMemory,
  DailySummary,
} from '../types'

// Sessions
export const fetchSessions = () =>
  client.get<Session[]>('/api/sessions').then((r) => r.data)

export const fetchSession = (id: number) =>
  client.get<Session>(`/api/sessions/${id}`).then((r) => r.data)

// Trades
export const fetchTrades = (sessionId?: number) =>
  client
    .get<Trade[]>('/api/trades', { params: sessionId ? { session_id: sessionId } : {} })
    .then((r) => r.data)

// Portfolio
export const fetchPortfolio = (sessionId?: number) =>
  client
    .get<PortfolioPosition[]>('/api/portfolio', {
      params: sessionId ? { session_id: sessionId } : {},
    })
    .then((r) => r.data)

// Decisions
export const fetchDecisions = (sessionId?: number, limit = 200) =>
  client
    .get<Decision[]>('/api/decisions', {
      params: { limit, ...(sessionId ? { session_id: sessionId } : {}) },
    })
    .then((r) => r.data)

// Tool calls
export const fetchToolCalls = (sessionId?: number, limit = 500) =>
  client
    .get<ToolCall[]>('/api/tool_calls', {
      params: { limit, ...(sessionId ? { session_id: sessionId } : {}) },
    })
    .then((r) => r.data)

// Journal
export const fetchJournal = (sessionId?: number) =>
  client
    .get<JournalEntry[]>('/api/journal', {
      params: sessionId ? { session_id: sessionId } : {},
    })
    .then((r) => r.data)

export const fetchJournalUpdates = (journalId: number) =>
  client.get<ThesisUpdate[]>(`/api/journal/${journalId}/updates`).then((r) => r.data)

// Memory
export const fetchMemory = (sessionId: number) =>
  client.get<SessionMemory>(`/api/memory/${sessionId}`).then((r) => r.data)

// Daily summary
export const fetchSummary = (sessionId?: number) =>
  client
    .get<DailySummary[]>('/api/summary', {
      params: sessionId ? { session_id: sessionId } : {},
    })
    .then((r) => r.data)

// ── Control API (write operations) ─────────────────────────────────────

export interface StartSessionParams {
  name: string
  execution_mode: 'paper' | 'live'
  trading_mode: 'safe' | 'balanced' | 'aggressive' | 'custom'
  starting_capital: number
  watchlist_path?: string
  allow_watchlist_adjustment?: boolean
  model?: string
  profit_reinvest_ratio?: number
  // Custom mode fields:
  custom_stop_loss?: number
  custom_take_profit?: number
  custom_max_positions?: number
  custom_max_per_trade?: number
  custom_max_deployed?: number
  custom_daily_loss_limit?: number
}

export const startSession = (params: StartSessionParams) =>
  client.post('/api/control/sessions/start', params).then((r) => r.data)

export const stopSession = (sessionId: number) =>
  client.post(`/api/control/sessions/${sessionId}/stop`).then((r) => r.data)

export const pauseSession = (sessionId: number) =>
  client.post(`/api/control/sessions/${sessionId}/pause`).then((r) => r.data)

export const resumeSession = (sessionId: number) =>
  client.post(`/api/control/sessions/${sessionId}/resume`).then((r) => r.data)

export const closeSession = (sessionId: number) =>
  client.post(`/api/control/sessions/${sessionId}/close`).then((r) => r.data)

export const syncPortfolio = (sessionId: number) =>
  client.post(`/api/control/sessions/${sessionId}/sync`).then((r) => r.data)

export const updateKiteToken = (token: string) =>
  client.post('/api/control/token', { token }).then((r) => r.data)

export const fetchPresets = () =>
  client.get('/api/control/presets').then((r) => r.data)

export const fetchRunning = () =>
  client.get<{ running_session_ids: number[] }>('/api/control/running').then((r) => r.data)

export async function updateReinvestRatio(sessionId: number, ratio: number) {
  const { data } = await client.post(`/api/control/sessions/${sessionId}/reinvest`, { ratio })
  return data
}

export async function updateSessionSettings(sessionId: number, settings: Record<string, any>): Promise<any> {
  const { data } = await client.put(`/api/control/sessions/${sessionId}/settings`, settings)
  return data
}
