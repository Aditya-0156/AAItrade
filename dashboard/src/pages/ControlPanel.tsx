import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Play,
  Square,
  Pause,
  RotateCcw,
  LogOut,
  Key,
  Plus,
  RefreshCw,
  Settings,
  Zap,
} from 'lucide-react'
import {
  fetchSessions,
  startSession,
  stopSession,
  pauseSession,
  resumeSession,
  closeSession,
  updateKiteToken,
  fetchPresets,
  fetchRunning,
  syncPortfolio,
} from '../api'
import type { StartSessionParams } from '../api'
import { StatusBadge, ModeBadge } from '../components/shared/StatusBadge'
import type { Session } from '../types'

function fmt(n: number) {
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

// ── New Session Form ────────────────────────────────────────────────────

function NewSessionForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<StartSessionParams>({
    name: '',
    execution_mode: 'paper',
    trading_mode: 'balanced',
    starting_capital: 20000,
  })

  const { data: presets } = useQuery({
    queryKey: ['presets'],
    queryFn: fetchPresets,
  })

  const mutation = useMutation({
    mutationFn: startSession,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
      onClose()
    },
  })

  const preset = presets?.[form.trading_mode]

  return (
    <div className="card">
      <div className="card-header">
        <span className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Plus size={14} /> New Session
        </span>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-xs">
          Cancel
        </button>
      </div>
      <div className="card-body space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Session Name</label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. live-balanced"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Starting Capital</label>
            <input
              type="number"
              value={form.starting_capital}
              onChange={(e) => setForm({ ...form, starting_capital: Number(e.target.value) })}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Execution Mode</label>
            <select
              value={form.execution_mode}
              onChange={(e) =>
                setForm({ ...form, execution_mode: e.target.value as 'paper' | 'live' })
              }
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500"
            >
              <option value="paper">Paper (Simulated)</option>
              <option value="live">Live (Real Money)</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Trading Mode</label>
            <select
              value={form.trading_mode}
              onChange={(e) =>
                setForm({
                  ...form,
                  trading_mode: e.target.value as 'safe' | 'balanced' | 'aggressive',
                })
              }
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500"
            >
              <option value="safe">Safe</option>
              <option value="balanced">Balanced</option>
              <option value="aggressive">Aggressive</option>
            </select>
          </div>
        </div>

        {/* Preset info */}
        {preset && (
          <div className="bg-gray-800/50 rounded p-3 text-xs text-gray-400 space-y-1">
            <div className="text-gray-300 font-medium mb-1">
              {form.trading_mode.charAt(0).toUpperCase() + form.trading_mode.slice(1)} Mode Rules
            </div>
            <div>Max per trade: {preset.max_per_trade}% | Stop: {preset.stop_loss}% | Target: {preset.take_profit}%</div>
            <div>Max positions: {preset.max_positions} | Max deployed: {preset.max_deployed}% | Daily limit: {preset.daily_loss_limit}%</div>
          </div>
        )}

        {mutation.isError && (
          <div className="text-red-400 text-xs bg-red-400/10 rounded p-2">
            {(mutation.error as Error)?.message || 'Failed to start session'}
          </div>
        )}

        <div className="flex gap-2">
          <button
            onClick={() => mutation.mutate(form)}
            disabled={mutation.isPending || !form.name}
            className="flex-1 bg-violet-600 hover:bg-violet-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm font-medium py-2 px-4 rounded flex items-center justify-center gap-2 transition-colors"
          >
            {mutation.isPending ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <Play size={14} />
            )}
            {form.execution_mode === 'live' ? 'Start Live Session' : 'Start Paper Session'}
          </button>
        </div>

        {form.execution_mode === 'live' && (
          <div className="text-amber-400 text-xs bg-amber-400/10 rounded p-2 flex items-center gap-2">
            <Zap size={12} /> This will trade with REAL MONEY on your Zerodha account.
          </div>
        )}
      </div>
    </div>
  )
}

// ── Token Update ────────────────────────────────────────────────────────

function TokenUpdate() {
  const [token, setToken] = useState('')
  const mutation = useMutation({
    mutationFn: (t: string) => updateKiteToken(t),
    onSuccess: () => setToken(''),
  })

  return (
    <div className="card">
      <div className="card-header">
        <span className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Key size={14} /> Kite Access Token
        </span>
      </div>
      <div className="card-body">
        <div className="flex gap-2">
          <input
            type="text"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Paste new Kite access token..."
            className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 font-mono focus:outline-none focus:border-violet-500"
          />
          <button
            onClick={() => mutation.mutate(token)}
            disabled={mutation.isPending || !token.trim()}
            className="bg-violet-600 hover:bg-violet-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm px-4 py-1.5 rounded flex items-center gap-2 transition-colors"
          >
            {mutation.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Key size={14} />}
            Update
          </button>
        </div>
        {mutation.isSuccess && (
          <div className="text-green-400 text-xs mt-2">Token updated successfully</div>
        )}
        {mutation.isError && (
          <div className="text-red-400 text-xs mt-2">
            {(mutation.error as Error)?.message || 'Update failed'}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Session Control Row ─────────────────────────────────────────────────

function SessionControlRow({ session }: { session: Session }) {
  const queryClient = useQueryClient()
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['sessions'] })

  const stopMut = useMutation({ mutationFn: () => stopSession(session.id), onSuccess: invalidate })
  const pauseMut = useMutation({ mutationFn: () => pauseSession(session.id), onSuccess: invalidate })
  const resumeMut = useMutation({ mutationFn: () => resumeSession(session.id), onSuccess: invalidate })
  const closeMut = useMutation({ mutationFn: () => closeSession(session.id), onSuccess: invalidate })
  const syncMut = useMutation({ mutationFn: () => syncPortfolio(session.id) })

  const isActive = session.status === 'active'
  const isPaused = session.status === 'paused'
  const isClosing = session.status === 'closing'
  const isRunning = isActive || isPaused || isClosing

  return (
    <div className="flex items-center justify-between py-3 border-b border-gray-800 last:border-0">
      <div className="flex items-center gap-3 min-w-0">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-200 truncate">
              {session.name || `Session #${session.id}`}
            </span>
            <StatusBadge status={session.status} />
            <ModeBadge mode={session.trading_mode} />
            <ModeBadge mode={session.execution_mode} />
          </div>
          <div className="text-xs text-gray-500 mt-0.5">
            Day {session.current_day} | Capital {fmt(session.current_capital)} | Started {session.started_at?.slice(0, 10)}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1.5 flex-shrink-0">
        {isActive && (
          <button
            onClick={() => pauseMut.mutate()}
            disabled={pauseMut.isPending}
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-yellow-400 transition-colors"
            title="Pause"
          >
            <Pause size={14} />
          </button>
        )}
        {isPaused && (
          <button
            onClick={() => resumeMut.mutate()}
            disabled={resumeMut.isPending}
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-green-400 transition-colors"
            title="Resume"
          >
            <Play size={14} />
          </button>
        )}
        {(isActive || isPaused) && (
          <button
            onClick={() => {
              if (window.confirm('Start closing mode? The AI will exit all positions over the next few days.')) {
                closeMut.mutate()
              }
            }}
            disabled={closeMut.isPending}
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-amber-400 transition-colors"
            title="Close (graceful exit)"
          >
            <LogOut size={14} />
          </button>
        )}
        {isRunning && (
          <button
            onClick={() => {
              if (window.confirm('Stop session immediately? Open positions will NOT be sold.')) {
                stopMut.mutate()
              }
            }}
            disabled={stopMut.isPending}
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-red-400 transition-colors"
            title="Stop (immediate halt)"
          >
            <Square size={14} />
          </button>
        )}
        {session.execution_mode === 'live' && isRunning && (
          <button
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-blue-400 transition-colors"
            title="Sync portfolio with Zerodha"
          >
            <RotateCcw size={14} className={syncMut.isPending ? 'animate-spin' : ''} />
          </button>
        )}
      </div>
    </div>
  )
}

// ── Main Control Panel ──────────────────────────────────────────────────

export function ControlPanel() {
  const [showNewSession, setShowNewSession] = useState(false)

  const { data: sessions = [] } = useQuery({
    queryKey: ['sessions'],
    queryFn: fetchSessions,
    refetchInterval: 10_000,
  })

  const { data: running } = useQuery({
    queryKey: ['running'],
    queryFn: fetchRunning,
    refetchInterval: 10_000,
  })

  const activeSessions = sessions.filter(
    (s) => s.status === 'active' || s.status === 'paused' || s.status === 'closing'
  )
  const pastSessions = sessions.filter(
    (s) => s.status === 'halted' || s.status === 'completed'
  )

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Settings size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
            Command Center
          </h2>
          {running && (
            <span className="text-xs bg-green-500/10 text-green-400 px-2 py-0.5 rounded">
              {running.running_session_ids.length} thread(s) running
            </span>
          )}
        </div>
        <button
          onClick={() => setShowNewSession(!showNewSession)}
          className="bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium py-1.5 px-3 rounded flex items-center gap-2 transition-colors"
        >
          <Plus size={14} />
          New Session
        </button>
      </div>

      {showNewSession && <NewSessionForm onClose={() => setShowNewSession(false)} />}

      <TokenUpdate />

      {/* Active Sessions */}
      <div className="card">
        <div className="card-header">
          <span className="text-sm font-semibold text-gray-300">
            Active Sessions ({activeSessions.length})
          </span>
        </div>
        <div className="card-body">
          {activeSessions.length > 0 ? (
            activeSessions.map((s) => <SessionControlRow key={s.id} session={s} />)
          ) : (
            <div className="text-sm text-gray-600 py-4 text-center">
              No active sessions. Start one above.
            </div>
          )}
        </div>
      </div>

      {/* Past Sessions */}
      {pastSessions.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="text-sm font-semibold text-gray-300">
              Past Sessions ({pastSessions.length})
            </span>
          </div>
          <div className="card-body">
            {pastSessions.slice(0, 10).map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between py-2 border-b border-gray-800 last:border-0"
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-400">
                    {s.name || `Session #${s.id}`}
                  </span>
                  <StatusBadge status={s.status} />
                  <ModeBadge mode={s.trading_mode} />
                </div>
                <div className="text-xs text-gray-500">
                  {s.started_at?.slice(0, 10)} — {s.ended_at?.slice(0, 10) || 'ongoing'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
