export interface Session {
  id: number
  name: string
  execution_mode: 'paper' | 'live'
  trading_mode: 'safe' | 'balanced' | 'aggressive'
  starting_capital: number
  current_capital: number
  secured_profit: number
  total_days: number
  current_day: number
  status: 'active' | 'paused' | 'closing' | 'halted' | 'completed' | 'error'
  started_at: string
  ended_at: string | null
}

export interface Trade {
  id: number
  session_id: number
  session_name: string
  symbol: string
  action: 'BUY' | 'SELL'
  quantity: number
  price: number
  stop_loss_price: number | null
  take_profit_price: number | null
  reason: string | null
  confidence: number | null
  executed_at: string
  pnl: number | null
}

export interface PortfolioPosition {
  id: number
  session_id: number
  session_name: string
  symbol: string
  quantity: number
  avg_price: number
  stop_loss_price: number | null
  take_profit_price: number | null
  opened_at: string
}

export interface Decision {
  id: number
  session_id: number
  session_name: string
  cycle_number: number
  action: 'BUY' | 'SELL' | 'HOLD' | 'WAIT'
  symbol: string | null
  quantity: number | null
  reason: string | null
  confidence: number | null
  flags: string | null   // JSON string array
  raw_json: string | null
  decided_at: string
}

export interface ToolCall {
  id: number
  session_id: number
  session_name: string
  cycle_number: number
  tool_name: string
  parameters: string | null
  result_summary: string | null
  called_at: string
}

export interface JournalEntry {
  id: number
  session_id: number
  session_name: string
  symbol: string
  entry_price: number
  reason: string | null
  news_cited: string | null   // JSON string array
  key_thesis: string | null
  target_price: number | null
  stop_price: number | null
  status: 'open' | 'closed' | 'stopped'
  opened_at: string
  closed_at: string | null
  exit_reason: string | null
  exit_price: number | null
  pnl: number | null
}

export interface ThesisUpdate {
  id: number
  journal_id: number
  session_id: number
  session_name: string
  symbol: string
  note: string
  updated_at: string
}

export interface SessionMemory {
  session_id: number
  content: string | null
  updated_at: string | null
  cycle_number: number | null
}

export interface DailySummary {
  id: number
  session_id: number
  session_name: string
  day_number: number
  date: string
  starting_capital: number
  ending_capital: number
  secured_profit: number
  trades_made: number
  wins: number
  losses: number
  total_pnl: number
  summary_text: string | null
}

export interface WsEvent {
  type: 'decision' | 'tool_call'
  id: number
  session_id: number
  session_name: string
  cycle_number: number
  // decision fields
  action?: string
  symbol?: string
  quantity?: number
  reason?: string
  confidence?: number
  flags?: string
  decided_at?: string
  // tool_call fields
  tool_name?: string
  parameters?: string
  result_summary?: string
  called_at?: string
}

export interface WsPayload {
  ts: string
  events: WsEvent[]
}
