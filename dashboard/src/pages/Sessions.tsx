import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  PieChart,
  Pie,
  Cell,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import { fetchSessions, fetchTrades, fetchPortfolio, fetchSummary } from '../api'
import { StatusBadge, ModeBadge } from '../components/shared/StatusBadge'
import { PnLBadge } from '../components/shared/PnLBadge'
import type { Session, PortfolioPosition, Trade, DailySummary } from '../types'

function toIST(str: string) {
  try {
    return new Date(str).toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return str
  }
}

function fmt(n: number) {
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

// Deployment pie chart
const PIE_COLORS = ['#6366f1', '#22c55e', '#f59e0b', '#06b6d4', '#ec4899', '#84cc16']

function DeploymentPie({
  positions,
  freeCash,
}: {
  positions: PortfolioPosition[]
  freeCash: number
}) {
  const data: { name: string; value: number }[] = [
    { name: 'Free Cash', value: freeCash },
    ...positions.map((p) => ({ name: p.symbol, value: p.quantity * p.avg_price })),
  ]

  if (data.every((d) => d.value === 0)) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
        No positions
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          innerRadius={60}
          outerRadius={90}
          paddingAngle={2}
          dataKey="value"
        >
          {data.map((_, index) => (
            <Cell key={`cell-${index}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            backgroundColor: '#111827',
            border: '1px solid #374151',
            borderRadius: '8px',
            fontSize: '12px',
          }}
          formatter={(value: number) => [fmt(value), '']}
        />
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: '12px', color: '#9ca3af' }}
        />
      </PieChart>
    </ResponsiveContainer>
  )
}

// P&L line chart from daily_summary
function PnLChart({ summaries }: { summaries: DailySummary[] }) {
  if (summaries.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
        No daily summary data yet
      </div>
    )
  }

  const data = summaries.map((s) => ({
    day: `Day ${s.day_number}`,
    capital: s.ending_capital,
    pnl: s.total_pnl,
  }))

  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="day" tick={{ fill: '#6b7280', fontSize: 11 }} />
        <YAxis
          tick={{ fill: '#6b7280', fontSize: 11 }}
          tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: '#111827',
            border: '1px solid #374151',
            borderRadius: '8px',
            fontSize: '12px',
          }}
          formatter={(value: number, name: string) => [fmt(value), name === 'capital' ? 'Capital' : 'Daily P&L']}
        />
        <Legend wrapperStyle={{ fontSize: '12px', color: '#9ca3af' }} />
        <Line
          type="monotone"
          dataKey="capital"
          stroke="#6366f1"
          strokeWidth={2}
          dot={{ fill: '#6366f1', r: 3 }}
          activeDot={{ r: 5 }}
        />
        <Line
          type="monotone"
          dataKey="pnl"
          stroke="#22c55e"
          strokeWidth={2}
          dot={{ fill: '#22c55e', r: 3 }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

// Positions table
function PositionsTable({ positions }: { positions: PortfolioPosition[] }) {
  if (positions.length === 0) {
    return <div className="py-8 text-center text-gray-600 text-sm">No open positions</div>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            {['Symbol', 'Qty', 'Entry Price', 'Cost', 'Stop Loss', 'Target', 'Opened'].map(
              (h) => (
                <th key={h} className="text-left py-2 px-3 text-xs text-gray-500 font-medium">
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const cost = p.quantity * p.avg_price
            return (
              <tr key={p.id} className="border-b border-gray-800/50 table-row-hover">
                <td className="py-2.5 px-3 font-mono font-semibold text-gray-100">{p.symbol}</td>
                <td className="py-2.5 px-3 font-mono text-gray-300">{p.quantity}</td>
                <td className="py-2.5 px-3 font-mono text-gray-300">{fmt(p.avg_price)}</td>
                <td className="py-2.5 px-3 font-mono text-gray-300">{fmt(cost)}</td>
                <td className="py-2.5 px-3 font-mono text-red-400">
                  {p.stop_loss_price ? fmt(p.stop_loss_price) : '—'}
                </td>
                <td className="py-2.5 px-3 font-mono text-emerald-400">
                  {p.take_profit_price ? fmt(p.take_profit_price) : '—'}
                </td>
                <td className="py-2.5 px-3 text-gray-500 text-xs">{toIST(p.opened_at)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// Trades table
function TradesTable({ trades }: { trades: Trade[] }) {
  if (trades.length === 0) {
    return <div className="py-8 text-center text-gray-600 text-sm">No trades yet</div>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            {['Action', 'Symbol', 'Qty', 'Price', 'P&L', 'Executed'].map((h) => (
              <th key={h} className="text-left py-2 px-3 text-xs text-gray-500 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-gray-800/50 table-row-hover">
              <td className="py-2.5 px-3">
                <span
                  className={`badge ${
                    t.action === 'BUY'
                      ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                      : 'bg-red-500/15 text-red-400 border border-red-500/30'
                  }`}
                >
                  {t.action}
                </span>
              </td>
              <td className="py-2.5 px-3 font-mono font-semibold text-gray-100">{t.symbol}</td>
              <td className="py-2.5 px-3 font-mono text-gray-300">{t.quantity}</td>
              <td className="py-2.5 px-3 font-mono text-gray-300">{fmt(t.price)}</td>
              <td className="py-2.5 px-3">
                <PnLBadge value={t.pnl} />
              </td>
              <td className="py-2.5 px-3 text-gray-500 text-xs">{toIST(t.executed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Sessions() {
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const { data: sessions = [], isLoading: loadingSessions } = useQuery({
    queryKey: ['sessions'],
    queryFn: fetchSessions,
    refetchInterval: 60_000,
  })

  const { data: positions = [], isLoading: loadingPositions } = useQuery({
    queryKey: ['portfolio', selectedId],
    queryFn: () => fetchPortfolio(selectedId ?? undefined),
    enabled: selectedId !== null,
    refetchInterval: 60_000,
  })

  const { data: trades = [], isLoading: loadingTrades } = useQuery({
    queryKey: ['trades', selectedId],
    queryFn: () => fetchTrades(selectedId ?? undefined),
    enabled: selectedId !== null,
    refetchInterval: 60_000,
  })

  const { data: summaries = [], isLoading: loadingSummary } = useQuery({
    queryKey: ['summary', selectedId],
    queryFn: () => fetchSummary(selectedId ?? undefined),
    enabled: selectedId !== null,
    refetchInterval: 60_000,
  })

  const selectedSession = sessions.find((s) => s.id === selectedId) ?? null

  return (
    <div className="p-6 space-y-6">
      {/* Session selector */}
      <div className="card">
        <div className="card-header">
          <span className="text-sm font-semibold text-gray-300">Select Session</span>
        </div>
        <div className="card-body">
          {loadingSessions ? (
            <div className="space-y-2">
              {[1, 2].map((i) => (
                <div key={i} className="skeleton h-10 w-full rounded-lg" />
              ))}
            </div>
          ) : sessions.length === 0 ? (
            <p className="text-gray-600 text-sm">No sessions found.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {sessions.map((s) => (
                <button
                  key={s.id}
                  onClick={() => setSelectedId(s.id)}
                  className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm transition-colors ${
                    selectedId === s.id
                      ? 'bg-gray-800 border-gray-600 text-gray-100'
                      : 'border-gray-800 text-gray-400 hover:border-gray-700 hover:text-gray-300'
                  }`}
                >
                  <StatusBadge status={s.status} />
                  <span>{s.name || `Session #${s.id}`}</span>
                  <ModeBadge mode={s.trading_mode} />
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {selectedSession && (
        <>
          {/* Session header */}
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-lg font-bold text-gray-100">
              {selectedSession.name || `Session #${selectedSession.id}`}
            </h2>
            <StatusBadge status={selectedSession.status} />
            <ModeBadge mode={selectedSession.trading_mode} />
            <ModeBadge mode={selectedSession.execution_mode} />
            <span className="text-xs text-gray-500">
              Day {selectedSession.current_day}/{selectedSession.total_days}
            </span>
          </div>

          {/* Charts row */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="card">
              <div className="card-header">
                <span className="text-sm font-semibold text-gray-300">Capital Deployment</span>
              </div>
              <div className="card-body">
                {loadingPositions ? (
                  <div className="skeleton h-48 w-full rounded" />
                ) : (
                  <DeploymentPie
                    positions={positions}
                    freeCash={selectedSession.current_capital}
                  />
                )}
              </div>
            </div>

            <div className="card">
              <div className="card-header">
                <span className="text-sm font-semibold text-gray-300">P&L by Day</span>
              </div>
              <div className="card-body">
                {loadingSummary ? (
                  <div className="skeleton h-48 w-full rounded" />
                ) : (
                  <PnLChart summaries={summaries} />
                )}
              </div>
            </div>
          </div>

          {/* Open positions */}
          <div className="card">
            <div className="card-header">
              <span className="text-sm font-semibold text-gray-300">Open Positions</span>
              <span className="text-xs text-gray-500">{positions.length} positions</span>
            </div>
            <div className="card-body p-0">
              {loadingPositions ? (
                <div className="p-5 space-y-2">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="skeleton h-10 w-full rounded" />
                  ))}
                </div>
              ) : (
                <PositionsTable positions={positions} />
              )}
            </div>
          </div>

          {/* Trade history */}
          <div className="card">
            <div className="card-header">
              <span className="text-sm font-semibold text-gray-300">Trade History</span>
              <span className="text-xs text-gray-500">{trades.length} trades</span>
            </div>
            <div className="card-body p-0">
              {loadingTrades ? (
                <div className="p-5 space-y-2">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="skeleton h-10 w-full rounded" />
                  ))}
                </div>
              ) : (
                <TradesTable trades={trades} />
              )}
            </div>
          </div>
        </>
      )}

      {!selectedSession && !loadingSessions && sessions.length > 0 && (
        <div className="text-center py-16 text-gray-600 text-sm">
          Select a session above to view details.
        </div>
      )}
    </div>
  )
}
