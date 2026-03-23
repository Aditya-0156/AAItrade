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
  X,
  Check,
  Pencil,
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
  updateSessionSettings,
} from '../api'
import type { StartSessionParams } from '../api'
import { StatusBadge, ModeBadge } from '../components/shared/StatusBadge'
import type { Session } from '../types'

function fmt(n: number) {
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

// ── Number input helper ──────────────────────────────────────────────────

function NumberField({
  label,
  description,
  value,
  unit,
  onChange,
}: {
  label: string
  description: string
  value: number
  unit: string
  onChange: (v: number) => void
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="text-xs text-gray-400">{label}</label>
        <span className="text-xs font-mono text-violet-300">
          {unit === '%' ? `${value}%` : unit === '₹' ? `₹${value.toLocaleString('en-IN')}` : unit === 'int' ? value : `${value}`}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="number"
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          step={unit === '%' ? 0.5 : unit === '₹' ? 1000 : 1}
          className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500"
        />
        {unit === '%' && <span className="text-xs text-gray-500">%</span>}
        {unit === '₹' && <span className="text-xs text-gray-500">₹</span>}
      </div>
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

            <NumberField
              label="Stop Loss %"
              description="Exit position automatically if it falls by this percentage. Lower = safer but more frequent stops."
              value={form.custom_stop_loss ?? 3.0}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_stop_loss: v })}
            />
            <NumberField
              label="Take Profit %"
              description="Lock in gains when position rises by this percentage. Lower = more frequent smaller wins."
              value={form.custom_take_profit ?? 5.0}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_take_profit: v })}
            />
            <NumberField
              label="Max Open Positions"
              description="Maximum number of stocks held simultaneously. More = diversified but harder to monitor."
              value={form.custom_max_positions ?? 5}
              unit="int"
              onChange={(v) => setForm({ ...form, custom_max_positions: v })}
            />
            <NumberField
              label="Max Per Trade %"
              description="Maximum % of capital that can be committed to a single trade. Limits concentration risk."
              value={form.custom_max_per_trade ?? 20.0}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_max_per_trade: v })}
            />
            <NumberField
              label="Max Deployed %"
              description="Maximum % of total capital that can be in open positions at once. Keeps cash buffer."
              value={form.custom_max_deployed ?? 90.0}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_max_deployed: v })}
            />
            <NumberField
              label="Daily Loss Limit %"
              description="Halt all trading for the day if total losses reach this % of capital. Hard circuit breaker."
              value={form.custom_daily_loss_limit ?? 5.0}
              unit="%"
              onChange={(v) => setForm({ ...form, custom_daily_loss_limit: v })}
            />
          </div>
        )}

        {/* Profit reinvest ratio — shown for ALL modes */}
        <div className="bg-gray-800/40 rounded-lg p-4 border border-gray-700">
          <label className="block text-xs text-gray-300 font-medium mb-2">Profit Reinvestment %</label>
          <div className="flex items-center gap-2 mb-2">
            <input
              type="number"
              min={0}
              max={100}
              step={5}
              value={Math.round((form.profit_reinvest_ratio ?? 0.5) * 100)}
              onChange={(e) => setForm({ ...form, profit_reinvest_ratio: Number(e.target.value) / 100 })}
              className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500"
            />
            <span className="text-xs text-gray-500">%</span>
          </div>
          <p className="text-xs text-gray-600">
            {reinvestLabel}
          </p>
          <p className="text-xs text-gray-600 mt-1.5">
            When selling at profit: 0% = secure all (keep safe); 100% = reinvest all (compound returns).
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

// ── Session Settings Editor ─────────────────────────────────────────────

function SessionSettingsEditor({
  session,
  onClose,
}: {
  session: Session
  onClose: () => void
}) {
  const queryClient = useQueryClient()

  // Build initial values from session's current settings, with sensible defaults
  const initial = {
    add_capital: 0,
    stop_loss_pct: session.stop_loss_pct ?? 3.0,
    take_profit_pct: session.take_profit_pct ?? 5.0,
    max_positions: session.max_positions ?? 5,
    max_per_trade_pct: session.max_per_trade_pct ?? 20.0,
    max_deployed_pct: session.max_deployed_pct ?? 90.0,
    daily_loss_limit_pct: session.daily_loss_limit_pct ?? 5.0,
    profit_reinvest_pct: Math.round((session.profit_reinvest_ratio ?? 0.5) * 100),
  }

  const [form, setForm] = useState(initial)
  const [notifyClaude, setNotifyClaude] = useState(true)
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)

  const mutation = useMutation({
    mutationFn: (payload: Record<string, any>) =>
      updateSessionSettings(session.id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
      setFeedback({ type: 'success', msg: 'Settings updated successfully.' })
    },
    onError: (err: Error) => {
      setFeedback({ type: 'error', msg: err.message || 'Failed to update settings.' })
    },
  })

  const handleApply = () => {
    setFeedback(null)

    // Only send fields that actually changed from the initial values
    const payload: Record<string, any> = {}

    if (form.add_capital > 0) payload.add_capital = form.add_capital
    if (form.stop_loss_pct !== initial.stop_loss_pct) payload.stop_loss_pct = form.stop_loss_pct
    if (form.take_profit_pct !== initial.take_profit_pct) payload.take_profit_pct = form.take_profit_pct
    if (form.max_positions !== initial.max_positions) payload.max_positions = form.max_positions
    if (form.max_per_trade_pct !== initial.max_per_trade_pct) payload.max_per_trade_pct = form.max_per_trade_pct
    if (form.max_deployed_pct !== initial.max_deployed_pct) payload.max_deployed_pct = form.max_deployed_pct
    if (form.daily_loss_limit_pct !== initial.daily_loss_limit_pct) payload.daily_loss_limit_pct = form.daily_loss_limit_pct
    if (form.profit_reinvest_pct !== initial.profit_reinvest_pct) {
      payload.profit_reinvest_ratio = form.profit_reinvest_pct / 100
    }

    if (Object.keys(payload).length === 0) {
      setFeedback({ type: 'error', msg: 'No changes to apply.' })
      return
    }

    payload.notify_claude = notifyClaude
    mutation.mutate(payload)
  }

  return (
    <div className="mt-3 bg-gray-800/60 rounded-lg border border-gray-700 p-4 space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Settings size={14} /> Edit Session Settings
        </span>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-gray-700 text-gray-500 hover:text-gray-300 transition-colors"
        >
          <X size={14} />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Add Capital */}
        <NumberField
          label="Add Capital"
          description="Extra capital to inject. Added to both starting and current capital."
          value={form.add_capital}
          unit="₹"
          onChange={(v) => setForm({ ...form, add_capital: v })}
        />

        {/* Stop Loss % */}
        <NumberField
          label="Stop Loss %"
          description="Exit position if it falls by this %. Lower = safer, more frequent stops."
          value={form.stop_loss_pct}
          unit="%"
          onChange={(v) => setForm({ ...form, stop_loss_pct: v })}
        />

        {/* Take Profit % */}
        <NumberField
          label="Take Profit %"
          description="Lock in gains when position rises by this %. Lower = smaller, more frequent wins."
          value={form.take_profit_pct}
          unit="%"
          onChange={(v) => setForm({ ...form, take_profit_pct: v })}
        />

        {/* Max Positions */}
        <NumberField
          label="Max Positions"
          description="Max stocks held at once. More = diversified but harder to monitor."
          value={form.max_positions}
          unit="int"
          onChange={(v) => setForm({ ...form, max_positions: v })}
        />

        {/* Max Per Trade % */}
        <NumberField
          label="Max Per Trade %"
          description="Max % of capital in a single trade. Limits concentration risk."
          value={form.max_per_trade_pct}
          unit="%"
          onChange={(v) => setForm({ ...form, max_per_trade_pct: v })}
        />

        {/* Max Deployed % */}
        <NumberField
          label="Max Deployed %"
          description="Max % of total capital in open positions at once. Keeps a cash buffer."
          value={form.max_deployed_pct}
          unit="%"
          onChange={(v) => setForm({ ...form, max_deployed_pct: v })}
        />

        {/* Daily Loss Limit % */}
        <NumberField
          label="Daily Loss Limit %"
          description="Halt all trading if daily losses hit this % of capital. Hard circuit breaker."
          value={form.daily_loss_limit_pct}
          unit="%"
          onChange={(v) => setForm({ ...form, daily_loss_limit_pct: v })}
        />

        {/* Profit Reinvestment % */}
        <NumberField
          label="Profit Reinvestment %"
          description={`${form.profit_reinvest_pct}% reinvested, ${100 - form.profit_reinvest_pct}% secured. 0 = secure all, 100 = compound all.`}
          value={form.profit_reinvest_pct}
          unit="%"
          onChange={(v) => setForm({ ...form, profit_reinvest_pct: Math.max(0, Math.min(100, v)) })}
        />
      </div>

      {/* Notify Claude checkbox */}
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={notifyClaude}
          onChange={(e) => setNotifyClaude(e.target.checked)}
          className="rounded border-gray-600 bg-gray-800 text-violet-500 focus:ring-violet-500"
        />
        <span className="text-xs text-gray-400">
          Notify Claude of changes (triggers a mini review of current thesis)
        </span>
      </label>

      {/* Feedback */}
      {feedback && (
        <div
          className={`text-xs rounded p-2 ${
            feedback.type === 'success'
              ? 'text-green-400 bg-green-400/10'
              : 'text-red-400 bg-red-400/10'
          }`}
        >
          {feedback.msg}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-2">
        <button
          onClick={handleApply}
          disabled={mutation.isPending}
          className="bg-violet-600 hover:bg-violet-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-xs font-medium px-4 py-1.5 rounded flex items-center gap-1.5 transition-colors"
        >
          {mutation.isPending ? (
            <RefreshCw size={12} className="animate-spin" />
          ) : (
            <Check size={12} />
          )}
          Apply Changes
        </button>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-200 text-xs px-3 py-1.5 rounded hover:bg-gray-700 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── Session Control Row ─────────────────────────────────────────────────

function SessionControlRow({ session }: { session: Session }) {
  const queryClient = useQueryClient()
  const [showReinvest, setShowReinvest] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
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
          {/* Edit settings */}
          {(session.status === 'active' || session.status === 'paused') && (
            <button
              onClick={() => { setShowSettings(!showSettings); if (!showSettings) setShowReinvest(false) }}
              className={`p-1.5 rounded hover:bg-gray-700 transition-colors ${showSettings ? 'text-violet-400' : 'text-gray-400 hover:text-violet-400'}`}
              title="Edit session settings"
            >
              <Pencil size={14} />
            </button>
          )}

          {/* Reinvest ratio toggle */}
          <button
            onClick={() => { setShowReinvest(!showReinvest); if (!showReinvest) setShowSettings(false) }}
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

      {/* Reinvest ratio input (expandable) */}
      {showReinvest && (
        <div className="mt-3 p-3 bg-gray-800/60 rounded-lg border border-gray-700">
          <div className="flex items-center gap-2 mb-2">
            <label className="text-xs text-gray-400 flex-1">Reinvest %</label>
            <input
              type="number"
              min={0}
              max={100}
              step={5}
              value={reinvestPct}
              onChange={(e) => setReinvestValue(Number(e.target.value) / 100)}
              className="w-16 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-violet-500"
            />
            <span className="text-xs text-gray-500">%</span>
          </div>
          <p className="text-xs text-gray-600 mb-2">
            {reinvestPct}% reinvested, {100 - reinvestPct}% secured
          </p>
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

      {/* Session settings editor (expandable) */}
      {showSettings && (
        <SessionSettingsEditor
          session={session}
          onClose={() => setShowSettings(false)}
        />
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
