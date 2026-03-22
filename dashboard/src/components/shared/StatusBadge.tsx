type Status = 'active' | 'paused' | 'halted' | 'completed' | 'error' | string

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30',
  paused: 'bg-yellow-500/15 text-yellow-400 border border-yellow-500/30',
  closing: 'bg-amber-500/15 text-amber-400 border border-amber-500/30',
  halted: 'bg-red-500/15 text-red-400 border border-red-500/30',
  completed: 'bg-blue-500/15 text-blue-400 border border-blue-500/30',
  error: 'bg-red-500/15 text-red-400 border border-red-500/30',
}

const DEFAULT_STYLE = 'bg-gray-700/50 text-gray-400 border border-gray-600/30'

interface StatusBadgeProps {
  status: Status
  className?: string
}

export function StatusBadge({ status, className = '' }: StatusBadgeProps) {
  const style = STATUS_STYLES[status.toLowerCase()] ?? DEFAULT_STYLE
  return (
    <span className={`badge ${style} ${className}`}>
      {status === 'active' && (
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1.5 animate-pulse" />
      )}
      {status === 'closing' && (
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400 mr-1.5 animate-pulse" />
      )}
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  )
}

type Mode = 'safe' | 'balanced' | 'aggressive' | 'paper' | 'live' | string

const MODE_STYLES: Record<string, string> = {
  safe: 'bg-blue-500/15 text-blue-400 border border-blue-500/30',
  balanced: 'bg-violet-500/15 text-violet-400 border border-violet-500/30',
  aggressive: 'bg-orange-500/15 text-orange-400 border border-orange-500/30',
  paper: 'bg-gray-700/50 text-gray-300 border border-gray-600/30',
  live: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30',
}

export function ModeBadge({ mode, className = '' }: { mode: Mode; className?: string }) {
  const style = MODE_STYLES[mode.toLowerCase()] ?? DEFAULT_STYLE
  return (
    <span className={`badge ${style} ${className}`}>
      {mode.charAt(0).toUpperCase() + mode.slice(1)}
    </span>
  )
}
