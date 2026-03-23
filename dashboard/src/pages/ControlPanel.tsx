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
  ChevronDown,
  ChevronUp,
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
  updateReinvestRatio,
} from '../api'
import type { StartSessionParams } from '../api'
import { StatusBadge, ModeBadge } from '../components/shared/StatusBadge'
import type { Session } from '../types'

function fmt(n: number) {
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

// ── Slider helper ────────────────────────────────────────────────────────

function SliderField({
  label,
  description,
  value,
  min,
  max,
  step,
  unit,
  onChange,
}: {
  label: string
  description: string
  value: number
  min: number
  max: number
  step: number
  unit: string
  onChange: (v: number) => void
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="text-xs text-gray-400">{label}</label>
        <span className="text-xs font-mono text-violet-300">
          {unit === '%' ? `${value}%` : unit === 'int' ? value : `${value}${unit}`}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-violet-500"
      />
      <p className="text-xs text-gray-600 mt-0.5">{description}</p>
    </div>
  )
}

// ── New Session Form ────────────────────────────────────────────────────

function NewSessionForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<StartSessionParams>({
    name: '',
    execution_mode: 'paper',
    trading_mode: 'balanced',
    starting_capital: 20000,
    profit_reinvest_ratio: 0.5,
    model: 'claude-haiku-4-5-20251001',
    custom_stop_loss: 3.0,
    custom_take_profit: 5.0,
    custom_max_positions: 5,
    custom_max_per_trade: 20.0,
    custom_max_deployed: 90.0,
    custom_daily_loss_limit: 5.0,
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

  const isCustom = form.trading_mode === 'custom'
  const preset = !isCustom ? presets?.[form.trading_mode] : null

  const reinvestPct = Math.round((form.profit_reinvest_ratio ?? 0.5) * 100)
  const reinvestLabel =
    reinvestPct === 0
      ? 'Secure All (0% reinvested)'
      : reinvestPct === 100
      ? 'Reinvest All (100% reinvested)'
      : `${reinvestPct}% reinvested, ${100 - reinvestPct}% secured`

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
      <div className="card-body space-y-5">

        {/* Row 1: Name + Capital */}
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
            <label className="block text-xs text-gray-500 mb-1">Starting Capital (₹)</label>
            <input
              type="number"
              value={form.starting_capital}
              onChange={(e) => setForm({ ...form, starting_capital: Number(e.target.value) })}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500"
            />
          </div>
        </div>

        {/* Row 2: Execution mode + Trading mode */}
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
            <p className="text-xs text-gray-600 mt-0.5">
              Paper mode simulates trades with no real money.
            </p>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Trading Mode</label>
            <select
              value={form.trading_mode}
              onChange={(e) =>
                setForm({
                  ...form,
                  trading_mode: e.target.value as 'safe' | 'balanced' | 'aggressive' | 'custom',
                })
              }
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500"
            >
              <option value="safe">Safe — low risk, preserve capital</option>
              <option value="balanced">Balanced — moderate risk/reward</option>
              <option value="aggressive">Aggressive — high risk, max growth</option>
              <option value="custom">Custom — configure every parameter</option>
            </select>
            <p className="text-xs text-gray-600 mt-0.5">
              Controls Claude's risk mandate and trade sizing rules.
            </p>
          </div>
        </div>

        {/* Model selector */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">AI Model</label>
          <select
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500"
          >
            <option value="claude-haiku-4-5-20251001">Haiku (Fast, Cheap) — recommended</option>
            <option value="claude-sonnet-4-5-20251001">Sonnet (Smarter, Slower)</option>
          </select>
          <p className="text-xs text-gray-600 mt-0.5">
            Haiku runs faster and costs ~73% less. Sonnet gives better reasoning for complex markets.
          </p>
        </div>

        {/* Preset info (non-custom) */}
        {preset && !isCustom && (
          <div className="bg-gray-800/50 rounded p-3 text-xs text-gray-400 space-y-1">
            <div className="text-gray-300 font-medium mb-1">
              {form.trading_mode.charAt(0).toUpperCase() + form.trading_mode.slice(1)} Mode Rules
            </div>
            <div>
              Stop loss: {preset.stop_loss}% | Take profit: {preset.take_profit}% | Max per trade: {preset.max_per_trade}%
            </div>
            <div>
              Max positions: {preset.max_positions} | Max deployed: {preset.max_deployed}% | Daily loss limit: {preset.daily_loss_limit}%
            </div>
          </div>
        )}

        {/* Custom mode sliders */}
        {isCustom && (
          <div className="bg-gray-800/40 rounded-lg p-4 space-y-4 border border-gray-700">
            <div className="text-xs font-semibold text-gray-300 mb-2">Custom Risk Parameters</div>

            <SliderField
              label="Stop Loss %"
              description="Exit position automatically if it falls by this percentage. Lower = safer but more frequent stops."
              value={form.custom_stop_loss ?? 3.0}
              min={0.5}
              max={15}
              step={0.5}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_stop_loss: v })}
            />
            <SliderField
              label="Take Profit %"
              description="Lock in gains when position rises by this percentage. Lower = more frequent smaller wins."
              value={form.custom_take_profit ?? 5.0}
              min={1}
              max={30}
              step={0.5}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_take_profit: v })}
            />
            <SliderField
              label="Max Open Positions"
              description="Maximum number of stocks held simultaneously. More = diversified but harder to monitor."
              value={form.custom_max_positions ?? 5}
              min={1}
              max={10}
              step={1}
              unit="int"
              onChange={(v) => setForm({ ...form, custom_max_positions: v })}
            />
            <SliderField
              label="Max Per Trade %"
              description="Maximum % of capital that can be committed to a single trade. Limits concentration risk."
              value={form.custom_max_per_trade ?? 20.0}
              min={5}
              max={50}
              step={1}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_max_per_trade: v })}
            />
            <SliderField
              label="Max Deployed %"
              description="Maximum % of total capital that can be in open positions at once. Keeps cash buffer."
              value={form.custom_max_deployed ?? 90.0}
              min={30}
              max={100}
              step={5}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_max_deployed: v })}
            />
            <SliderField
              label="Daily Loss Limit %"
              description="Halt all trading for the day if total losses reach this % of capital. Hard circuit breaker."
              value={form.custom_daily_loss_limit ?? 5.0}
              min={1}
              max={20}
              step={0.5}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_daily_loss_limit: v })}
            />
          </div>
        )}

        {/* Profit reinvest ratio — shown for ALL modes */}
        <div className="bg-gray-800/40 rounded-lg p-4 border border-gray-700">
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-300 font-medium">Profit Reinvestment Ratio</label>
            <span className="text-xs font-mono text-violet-300">{reinvestLabel}</span>
          </div>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={form.profit_reinvest_ratio ?? 0.5}
            onChange={(e) => setForm({ ...form, profit_reinvest_ratio: Number(e.target.value) })}
            className="w-full accent-violet-500"
          />
          <div className="flex justify-between text-xs text-gray-600 mt-0.5">
            <span>Secure All</span>
            <span>50/50 Split</span>
            <span>Reinvest All</span>
          </div>
          <p className="text-xs text-gray-600 mt-1.5">
            When a position is sold at a profit, this controls how much goes back into free cash
            vs. secured (locked, not re-traded). 0% = all profit locked away; 100% = all profit
            returned to trading capital.
          </p>
        </div>

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
  const [showReinvest, setShowReinvest] = useState(false)
  const [reinvestValue, setReinvestValue] = useState(session.profit_reinvest_ratio ?? 0.5)
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['sessions'] })

  const stopMut = useMutation({ mutationFn: () => stopSession(session.id), onSuccess: invalidate })
  const pauseMut = useMutation({ mutationFn: () => pauseSession(session.id), onSuccess: invalidate })
  const resumeMut = useMutation({ mutationFn: () => resumeSession(session.id), onSuccess: invalidate })
  const closeMut = useMutation({ mutationFn: () => closeSession(session.id), onSuccess: invalidate })
  const syncMut = useMutation({ mutationFn: () => syncPortfolio(session.id) })
  const reinvestMut = useMutation({
    mutationFn: (ratio: number) => updateReinvestRatio(session.id, ratio),
    onSuccess: invalidate,
  })

  const isActive = session.status === 'active'
  const isPaused = session.status === 'paused'
  const isClosing = session.status === 'closing'
  const isRunning = isActive || isPaused || isClosing

  const reinvestPct = Math.round(reinvestValue * 100)

  return (
    <div className="py-3 border-b border-gray-800 last:border-0">
      <div className="flex items-center justify-between">
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
          {/* Reinvest ratio toggle */}
          <button
            onClick={() => setShowReinvest(!showReinvest)}
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-violet-400 transition-colors"
            title="Adjust reinvest ratio"
          >
            {showReinvest ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

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

      {/* Reinvest ratio slider (expandable) */}
      {showReinvest && (
        <div className="mt-3 p-3 bg-gray-800/60 rounded-lg border border-gray-700">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-gray-400">Profit Reinvest Ratio</span>
            <span className="text-xs font-mono text-violet-300">
              {reinvestPct}% reinvested · {100 - reinvestPct}% secured
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={reinvestValue}
            onChange={(e) => setReinvestValue(Number(e.target.value))}
            className="w-full accent-violet-500"
          />
          <div className="flex justify-between text-xs text-gray-600 mt-0.5 mb-2">
            <span>Secure All</span>
            <span>50/50</span>
            <span>Reinvest All</span>
          </div>
          <button
            onClick={() => reinvestMut.mutate(reinvestValue)}
            disabled={reinvestMut.isPending}
            className="text-xs bg-violet-600 hover:bg-violet-500 disabled:bg-gray-700 text-white px-3 py-1 rounded flex items-center gap-1.5 transition-colors"
          >
            {reinvestMut.isPending ? <RefreshCw size={11} className="animate-spin" /> : null}
            Apply
          </button>
          {reinvestMut.isSuccess && (
            <span className="text-xs text-green-400 ml-2">Saved</span>
          )}
        </div>
      )}
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
