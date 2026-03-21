import { useEffect, useState } from 'react'
import { Wifi, WifiOff, Clock, RefreshCw } from 'lucide-react'
import { useAppStore } from '../../store'

function toIST(date: Date): string {
  return date.toLocaleTimeString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function toISTDate(date: Date): string {
  return date.toLocaleDateString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

interface TopBarProps {
  title: string
}

export function TopBar({ title }: TopBarProps) {
  const { wsStatus, lastUpdated } = useAppStore()
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(interval)
  }, [])

  const wsColor =
    wsStatus === 'connected'
      ? 'text-emerald-400'
      : wsStatus === 'connecting'
      ? 'text-yellow-400'
      : 'text-red-400'

  const wsLabel =
    wsStatus === 'connected'
      ? 'Live'
      : wsStatus === 'connecting'
      ? 'Connecting...'
      : 'Disconnected'

  return (
    <header className="h-14 bg-gray-950 border-b border-gray-800 flex items-center justify-between px-6 flex-shrink-0">
      <h1 className="text-base font-semibold text-gray-100">{title}</h1>

      <div className="flex items-center gap-5 text-xs text-gray-400">
        {/* Last updated */}
        {lastUpdated && (
          <div className="flex items-center gap-1.5">
            <RefreshCw size={12} className="text-gray-600" />
            <span>Updated {toIST(lastUpdated)}</span>
          </div>
        )}

        {/* IST clock */}
        <div className="flex items-center gap-1.5">
          <Clock size={12} className="text-gray-600" />
          <span className="font-mono">
            {toISTDate(now)} {toIST(now)} IST
          </span>
        </div>

        {/* WS status */}
        <div className={`flex items-center gap-1.5 ${wsColor}`}>
          {wsStatus === 'connected' ? <Wifi size={13} /> : <WifiOff size={13} />}
          <span>{wsLabel}</span>
          {wsStatus === 'connected' && (
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          )}
        </div>
      </div>
    </header>
  )
}
