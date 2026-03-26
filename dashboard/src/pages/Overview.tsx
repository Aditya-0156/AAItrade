import { useQuery } from '@tanstack/react-query'
import { TrendingUp, TrendingDown, Layers, Calendar, DollarSign, Radio, FlaskConical } from 'lucide-react'
import { fetchSessions, fetchPortfolio } from '../api'
import { StatusBadge, ModeBadge } from '../components/shared/StatusBadge'
import { PnLPercent } from '../components/shared/PnLBadge'
import type { Session, PortfolioPosition } from '../types'

function fmt(n: number) {
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

function calcDeployed(positions: PortfolioPosition[], sessionId: number): number {
  return positions
    .filter((p) => p.session_id === sessionId)
    .reduce((sum, p) => sum + p.quantity * p.avg_price, 0)
}

function SessionCard({ session, allPositions }: { session: Session; allPositions: PortfolioPosition[] }) {
  const deployed = calcDeployed(allPositions, session.id)
  const totalValue = session.current_capital + deployed + session.secured_profit
  const pnlAbs = totalValue - session.starting_capital
  const pnlPct = session.starting_capital > 0 ? (pnlAbs / session.starting_capital) * 100 : 0
  const deployedPct = totalValue > 0 ? (deployed / totalValue) * 100 : 0

  return (
    <div className="card flex flex-col gap-0 overflow-hidden">
      {/* Header */}
      <div className="card-header">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-gray-100 text-sm">{session.name || `Session #${session.id}`}</span>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <ModeBadge mode={session.trading_mode} />
            <ModeBadge mode={session.execution_mode} />
            <StatusBadge status={session.status} />
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs text-gray-500 flex items-center gap-1 justify-end">
            <Calendar size={11} />
            Day {session.current_day}{session.total_days < 99999 ? `/${session.total_days}` : ''}
          </div>
        </div>
      </div>

      {/* Metrics */}
      <div className="card-body grid grid-cols-2 gap-4">
        {/* Capital */}
        <div>
          <div className="text-xs text-gray-500 mb-1">Total Value</div>
          <div className="text-lg font-bold font-mono text-gray-100">{fmt(totalValue)}</div>
          <div className="text-xs text-gray-500 mt-0.5">Started {fmt(session.starting_capital)}</div>
        </div>

        {/* P&L */}
        <div>
          <div className="text-xs text-gray-500 mb-1">P&L</div>
          <div className="text-lg font-bold font-mono">
            <PnLPercent value={pnlPct} />
          </div>
          <div className={`text-xs mt-0.5 font-mono ${pnlAbs >= 0 ? 'text-profit' : 'text-loss'}`}>
            {pnlAbs >= 0 ? '+' : ''}₹{Math.abs(pnlAbs).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
          </div>
          {session.secured_profit > 0 && (
            <div className="text-xs mt-0.5 font-mono text-amber-400">
              ₹{session.secured_profit.toLocaleString('en-IN', { maximumFractionDigits: 0 })} secured
            </div>
          )}
        </div>

        {/* Cash */}
        <div>
          <div className="text-xs text-gray-500 mb-1 flex items-center gap-1">
            <DollarSign size={10} /> Free Cash
          </div>
          <div className="text-sm font-mono text-gray-200">{fmt(session.current_capital)}</div>
        </div>

        {/* Deployed */}
        <div>
          <div className="text-xs text-gray-500 mb-1 flex items-center gap-1">
            <Layers size={10} /> Deployed
          </div>
          <div className="text-sm font-mono text-gray-200">{fmt(deployed)}</div>
          <div className="mt-1.5 h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-violet-500 rounded-full transition-all duration-500"
              style={{ width: `${Math.min(deployedPct, 100)}%` }}
            />
          </div>
          <div className="text-xs text-gray-600 mt-0.5">{deployedPct.toFixed(0)}% deployed</div>
        </div>
      </div>
    </div>
  )
}

function SkeletonCard() {
  return (
    <div className="card">
      <div className="card-header">
        <div className="space-y-2">
          <div className="skeleton h-4 w-32" />
          <div className="skeleton h-3 w-24" />
        </div>
      </div>
      <div className="card-body grid grid-cols-2 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i}>
            <div className="skeleton h-3 w-16 mb-2" />
            <div className="skeleton h-5 w-24" />
          </div>
        ))}
      </div>
    </div>
  )
}

function CombinedStats({
  sessions,
  positions,
  label,
  labelColor,
  icon,
}: {
  sessions: Session[]
  positions: PortfolioPosition[]
  label: string
  labelColor: string
  icon: React.ReactNode
}) {
  const totalStarting = sessions.reduce((s, x) => s + x.starting_capital, 0)
  const totalDeployed = sessions.reduce((s, x) => s + calcDeployed(positions, x.id), 0)
  const totalCash = sessions.reduce((s, x) => s + x.current_capital, 0)
  const totalSecured = sessions.reduce((s, x) => s + x.secured_profit, 0)
  const totalValue = totalCash + totalDeployed + totalSecured
  const totalPnl = totalValue - totalStarting
  const totalPnlPct = totalStarting > 0 ? (totalPnl / totalStarting) * 100 : 0
  const activeSessions = sessions.filter((s) => s.status === 'active').length

  return (
    <div className="card">
      <div className="card-header">
        <span className={`text-sm font-semibold flex items-center gap-2 ${labelColor}`}>
          {icon}
          {label}
        </span>
        <span className="text-xs text-gray-500">{sessions.length} sessions · {activeSessions} active</span>
      </div>
      <div className="card-body flex flex-wrap gap-8">
        <div>
          <div className="text-xs text-gray-500 mb-1">Total Value</div>
          <div className="text-2xl font-bold font-mono text-gray-100">{fmt(totalValue)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500 mb-1">Overall P&L</div>
          <div className="text-2xl font-bold font-mono">
            <PnLPercent value={totalPnlPct} />
          </div>
          <div className={`text-sm font-mono mt-0.5 ${totalPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
            {totalPnl >= 0 ? '+' : ''}₹{Math.abs(totalPnl).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
          </div>
          {totalSecured > 0 && (
            <div className="text-sm font-mono mt-0.5 text-amber-400">
              ₹{totalSecured.toLocaleString('en-IN', { maximumFractionDigits: 0 })} secured
            </div>
          )}
        </div>
        <div>
          <div className="text-xs text-gray-500 mb-1">Cash Available</div>
          <div className="text-xl font-mono text-gray-200">{fmt(totalCash)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500 mb-1">Deployed Capital</div>
          <div className="text-xl font-mono text-gray-200">{fmt(totalDeployed)}</div>
        </div>
      </div>
    </div>
  )
}

function SessionGroup({
  title,
  sessions,
  allPositions,
  isLoading,
}: {
  title: string
  sessions: Session[]
  allPositions: PortfolioPosition[]
  isLoading: boolean
}) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <TrendingUp size={15} className="text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">{title}</h2>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {isLoading ? (
          [1, 2, 3].map((i) => <SkeletonCard key={i} />)
        ) : sessions.length > 0 ? (
          sessions.map((session) => (
            <SessionCard key={session.id} session={session} allPositions={allPositions} />
          ))
        ) : (
          <div className="col-span-3 py-10 text-center text-gray-600 text-sm">
            No {title.toLowerCase()} sessions found.
          </div>
        )}
      </div>
    </div>
  )
}

export function Overview() {
  const { data: sessions, isLoading: loadingSessions, isError: errorSessions } = useQuery({
    queryKey: ['sessions'],
    queryFn: fetchSessions,
    refetchInterval: 60_000,
  })

  const { data: allPositions = [], isLoading: loadingPositions } = useQuery({
    queryKey: ['portfolio'],
    queryFn: () => fetchPortfolio(),
    refetchInterval: 60_000,
  })

  const isLoading = loadingSessions || loadingPositions

  if (errorSessions) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <TrendingDown className="text-red-400" size={32} />
        <p className="text-gray-400 text-sm">Could not load sessions. Is the API running?</p>
      </div>
    )
  }

  const liveSessions = sessions?.filter((s) => s.execution_mode === 'live') ?? []
  const paperSessions = sessions?.filter((s) => s.execution_mode === 'paper') ?? []

  return (
    <div className="p-6 space-y-6">
      {/* Live combined stats */}
      {!isLoading && liveSessions.length > 0 && (
        <CombinedStats
          sessions={liveSessions}
          positions={allPositions}
          label="Live Trading"
          labelColor="text-green-400"
          icon={<Radio size={14} />}
        />
      )}

      {/* Paper combined stats */}
      {!isLoading && paperSessions.length > 0 && (
        <CombinedStats
          sessions={paperSessions}
          positions={allPositions}
          label="Paper Trading"
          labelColor="text-gray-400"
          icon={<FlaskConical size={14} />}
        />
      )}

      {/* Live sessions grid */}
      {!isLoading && liveSessions.length > 0 && (
        <SessionGroup
          title="Live Sessions"
          sessions={liveSessions}
          allPositions={allPositions}
          isLoading={isLoading}
        />
      )}

      {/* Paper sessions grid */}
      {!isLoading && paperSessions.length > 0 && (
        <SessionGroup
          title="Paper Sessions"
          sessions={paperSessions}
          allPositions={allPositions}
          isLoading={isLoading}
        />
      )}

      {/* Loading skeleton */}
      {isLoading && (
        <div>
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Sessions</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => <SkeletonCard key={i} />)}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && (!sessions || sessions.length === 0) && (
        <div className="py-16 text-center text-gray-600 text-sm">
          No sessions found. Start a trading session to see data here.
        </div>
      )}
    </div>
  )
}
