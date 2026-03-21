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
