import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity as ActivityIcon, Filter, TrendingUp, TrendingDown, Minus, Wrench, Settings, ChevronDown, ChevronUp } from 'lucide-react'
import { fetchSessions, fetchDecisions, fetchToolCalls } from '../api'
import type { Session, Decision, ToolCall } from '../types'

function toIST(str: string) {
  try {
    return new Date(str).toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return str
  }
}

function parseFlags(flags: string | null): string[] {
  if (!flags) return []
  try {
    const parsed = JSON.parse(flags)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return [flags]
  }
}

function ActionIcon({ action }: { action: string }) {
  switch (action?.toUpperCase()) {
    case 'BUY':
      return <TrendingUp size={14} className="text-emerald-400" />
    case 'SELL':
      return <TrendingDown size={14} className="text-red-400" />
    case 'HOLD':
    case 'WAIT':
      return <Minus size={14} className="text-blue-400" />
    default:
      return <ActivityIcon size={14} className="text-gray-400" />
  }
}

function actionColor(action: string): string {
  switch (action?.toUpperCase()) {
    case 'BUY':
      return 'border-l-emerald-500 bg-emerald-500/5'
    case 'SELL':
      return 'border-l-red-500 bg-red-500/5'
    case 'HOLD':
    case 'WAIT':
      return 'border-l-blue-500 bg-blue-500/5'
    default:
      return 'border-l-gray-700 bg-gray-800/30'
  }
}

function DecisionRow({ decision }: { decision: Decision }) {
  const [expanded, setExpanded] = useState(false)
  const flags = parseFlags(decision.flags)
  const isSettingsUpdate = flags.includes('SETTINGS_UPDATE')
  const color = isSettingsUpdate
    ? 'border-l-violet-500 bg-violet-500/5'
    : actionColor(decision.action)

  return (
    <div className={`border-l-2 pl-3 py-2 pr-3 rounded-r-lg ${color} mb-1.5`}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          {isSettingsUpdate ? (
            <Settings size={14} className="text-violet-400" />
          ) : (
            <ActionIcon action={decision.action} />
          )}
          <span
            className={`font-semibold text-sm ${
              isSettingsUpdate
                ? 'text-violet-400'
                : decision.action === 'BUY'
                ? 'text-emerald-400'
                : decision.action === 'SELL'
                ? 'text-red-400'
                : decision.action === 'HOLD' || decision.action === 'WAIT'
                ? 'text-blue-400'
                : 'text-gray-400'
            }`}
          >
            {isSettingsUpdate ? 'SETTINGS' : decision.action}
          </span>
          {decision.symbol && (
            <span className="font-mono text-sm font-semibold text-gray-100">
              {decision.symbol}
            </span>
          )}
          {decision.quantity != null && (
            <span className="text-xs text-gray-500">×{decision.quantity}</span>
          )}
          {decision.confidence != null && (
            <span className="text-xs text-gray-600 bg-gray-800 px-1.5 py-0.5 rounded">
              {decision.confidence}% conf
            </span>
          )}
          {flags.map((f) => (
            <span
              key={f}
              className="text-xs bg-yellow-500/10 text-yellow-400 px-1.5 py-0.5 rounded border border-yellow-500/20"
            >
              {f}
            </span>
          ))}
        </div>
        <div className="flex items-start gap-2 flex-shrink-0">
          <div className="text-right">
            <div className="text-xs text-gray-500">{toIST(decision.decided_at)}</div>
            <div className="text-xs text-gray-600">
              {decision.session_name} · C{decision.cycle_number}
            </div>
          </div>
          {decision.reason && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="mt-0.5 text-gray-600 hover:text-gray-400 transition-colors"
            >
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          )}
        </div>
      </div>
      {decision.reason && (
        <p className={`text-xs text-gray-400 mt-1 leading-relaxed whitespace-pre-wrap ${expanded ? '' : 'line-clamp-2'}`}>
          {decision.reason}
        </p>
      )}
    </div>
  )
}

function ToolCallRow({ tc }: { tc: ToolCall }) {
  const [expanded, setExpanded] = useState(false)
  let paramsPreview = tc.parameters ?? ''
  try {
    const p = JSON.parse(tc.parameters ?? '{}')
    paramsPreview = Object.entries(p)
      .slice(0, 3)
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
      .join(', ')
  } catch {
    paramsPreview = tc.parameters?.slice(0, 80) ?? ''
  }

  return (
    <div className="border-l-2 border-l-gray-700 bg-gray-800/20 pl-3 py-2 pr-3 rounded-r-lg mb-1.5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-2 min-w-0">
          <Wrench size={13} className="text-gray-500 flex-shrink-0" />
          <span className="font-mono text-sm text-gray-300 font-medium">{tc.tool_name}</span>
          {paramsPreview && (
            <span className="text-xs text-gray-600 truncate max-w-xs">{paramsPreview}</span>
          )}
        </div>
        <div className="flex items-start gap-2 flex-shrink-0">
          <div className="text-right">
            <div className="text-xs text-gray-500">{toIST(tc.called_at)}</div>
            <div className="text-xs text-gray-600">
              {tc.session_name} · C{tc.cycle_number}
            </div>
          </div>
          {tc.result_summary && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="mt-0.5 text-gray-600 hover:text-gray-400 transition-colors"
            >
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          )}
        </div>
      </div>
      {tc.result_summary && (
        <p className={`text-xs text-gray-500 mt-1 leading-relaxed whitespace-pre-wrap ${expanded ? '' : 'line-clamp-2'}`}>
          {tc.result_summary}
        </p>
      )}
    </div>
  )
}

type FeedItemType = 'all' | 'decisions' | 'tool_calls' | 'BUY' | 'SELL' | 'HOLD'

export function Activity() {
  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null)
  const [typeFilter, setTypeFilter] = useState<FeedItemType>('all')

  const { data: sessions = [] } = useQuery({
    queryKey: ['sessions'],
    queryFn: fetchSessions,
  })

  // Fetch decisions and tool calls with 30s polling
  const { data: decisions = [], isLoading: loadingDecisions } = useQuery({
    queryKey: ['decisions', selectedSessionId],
    queryFn: () => fetchDecisions(selectedSessionId ?? undefined, 500),
    refetchInterval: 30_000, // Poll every 30 seconds
    staleTime: 5_000,
  })

  const { data: toolCalls = [], isLoading: loadingToolCalls } = useQuery({
    queryKey: ['tool_calls', selectedSessionId],
    queryFn: () => fetchToolCalls(selectedSessionId ?? undefined, 1000),
    refetchInterval: 30_000, // Poll every 30 seconds
    staleTime: 5_000,
  })

  const isLoading = loadingDecisions || loadingToolCalls

  type FeedItem =
    | { kind: 'decision'; ts: string; data: Decision }
    | { kind: 'tool_call'; ts: string; data: ToolCall }

  const mergedFeed = useMemo<FeedItem[]>(() => {
    const items: FeedItem[] = [
      ...decisions.map((d) => ({ kind: 'decision' as const, ts: d.decided_at, data: d })),
      ...toolCalls.map((tc) => ({ kind: 'tool_call' as const, ts: tc.called_at, data: tc })),
    ]
    items.sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime())
    return items
  }, [decisions, toolCalls])

  const filteredFeed = useMemo(() => {
    return mergedFeed.filter((item) => {
      if (typeFilter === 'all') return true
      if (typeFilter === 'decisions') return item.kind === 'decision'
      if (typeFilter === 'tool_calls') return item.kind === 'tool_call'
      if (item.kind === 'decision') {
        return (item.data as Decision).action?.toUpperCase() === typeFilter
      }
      return false
    })
  }, [mergedFeed, typeFilter])


  return (
    <div className="p-6 space-y-4">
      {/* Filters */}
      <div className="card">
        <div className="card-body flex flex-wrap gap-4 items-center">
          {/* Session filter */}
          <div className="flex items-center gap-2">
            <Filter size={13} className="text-gray-500" />
            <span className="text-xs text-gray-500">Session:</span>
            <select
              className="bg-gray-800 border border-gray-700 text-gray-300 text-sm rounded-lg px-2 py-1 focus:outline-none focus:ring-1 focus:ring-gray-600"
              value={selectedSessionId ?? ''}
              onChange={(e) =>
                setSelectedSessionId(e.target.value ? parseInt(e.target.value) : null)
              }
            >
              <option value="">All Sessions</option>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || `Session #${s.id}`}
                </option>
              ))}
            </select>
          </div>

          {/* Type filter */}
          <div className="flex items-center gap-1">
            {(
              [
                { value: 'all', label: 'All' },
                { value: 'decisions', label: 'Decisions' },
                { value: 'tool_calls', label: 'Tool Calls' },
                { value: 'BUY', label: 'BUY' },
                { value: 'SELL', label: 'SELL' },
                { value: 'HOLD', label: 'HOLD' },
              ] as { value: FeedItemType; label: string }[]
            ).map(({ value, label }) => (
              <button
                key={value}
                onClick={() => setTypeFilter(value)}
                className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
                  typeFilter === value
                    ? 'bg-gray-700 text-gray-100'
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="ml-auto text-xs text-gray-600">
            {filteredFeed.length} items
          </div>
        </div>
      </div>

      {/* Feed */}
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-2">
            <ActivityIcon size={14} className="text-gray-500" />
            <span className="text-sm font-semibold text-gray-300">Activity Feed</span>
          </div>
        </div>
        <div className="card-body">
          {isLoading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="skeleton h-14 w-full rounded-lg" />
              ))}
            </div>
          ) : filteredFeed.length === 0 ? (
            <div className="py-12 text-center text-gray-600 text-sm">
              No activity found for the selected filters.
            </div>
          ) : (
            <div className="max-h-[70vh] overflow-y-auto pr-1">
              {filteredFeed.map((item) =>
                item.kind === 'decision' ? (
                  <DecisionRow key={`d-${item.data.id}`} decision={item.data as Decision} />
                ) : (
                  <ToolCallRow key={`tc-${item.data.id}`} tc={item.data as ToolCall} />
                ),
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
