import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Wallet, Cpu, Receipt, TrendingDown, TrendingUp, Plus } from 'lucide-react'
import {
  fetchExpenseSummary,
  fetchDailyCosts,
  fetchApiUsage,
  fetchExpenseList,
  addManualExpense,
} from '../api'

const inr = (n: number) =>
  `₹${(n ?? 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

function StatTile({
  label,
  value,
  sub,
  icon: Icon,
  tone = 'neutral',
}: {
  label: string
  value: string
  sub?: string
  icon: any
  tone?: 'neutral' | 'good' | 'bad'
}) {
  const toneClass =
    tone === 'good' ? 'text-emerald-400' : tone === 'bad' ? 'text-rose-400' : 'text-gray-100'
  return (
    <div className="card">
      <div className="card-body">
        <div className="flex items-center gap-2 text-xs text-gray-500 mb-1.5">
          <Icon size={13} />
          {label}
        </div>
        <div className={`text-xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
        {sub && <div className="text-xs text-gray-600 mt-1">{sub}</div>}
      </div>
    </div>
  )
}

export function Expenses() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ label: '', amount_inr: 0 })

  const { data: summary } = useQuery({
    queryKey: ['expense-summary'],
    queryFn: () => fetchExpenseSummary(),
    refetchInterval: 60_000,
  })
  const { data: daily } = useQuery({ queryKey: ['expense-daily'], queryFn: () => fetchDailyCosts(14) })
  const { data: usage } = useQuery({ queryKey: ['api-usage'], queryFn: () => fetchApiUsage(undefined, 25) })
  const { data: list } = useQuery({ queryKey: ['expense-list'], queryFn: fetchExpenseList })

  const mutation = useMutation({
    mutationFn: addManualExpense,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['expense-summary'] })
      queryClient.invalidateQueries({ queryKey: ['expense-list'] })
      setForm({ label: '', amount_inr: 0 })
    },
  })

  const s = summary
  const net = s?.net_profit_after_all_expenses ?? 0
  const maxDay = Math.max(
    1,
    ...(daily?.days ?? []).map((d: any) => (d.api_cost ?? 0) + (d.trading_charges ?? 0)),
  )

  return (
    <div className="p-6 space-y-6">
      {/* Headline: net profit after EVERYTHING */}
      <div className="card">
        <div className="card-body">
          <div className="text-xs text-gray-500 mb-1">Net Profit — after trading charges, API cost, and subscriptions</div>
          <div className={`text-3xl font-bold tabular-nums ${net >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {inr(net)}
          </div>
          <div className="text-xs text-gray-600 mt-1.5">
            Realised P&amp;L {inr(s?.realised_pnl_net_of_charges ?? 0)} − API {inr(s?.api_cost ?? 0)} − subscriptions{' '}
            {inr(s?.subscriptions ?? 0)}
            {s?.other ? ` − other ${inr(s.other)}` : ''}
          </div>
        </div>
      </div>

      {/* Cost tiles */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile
          label="Trading Charges"
          value={inr(s?.trading_charges ?? 0)}
          sub={`${s?.trade_count ?? 0} trades (STT, stamp, DP, GST)`}
          icon={Receipt}
          tone="bad"
        />
        <StatTile
          label="Claude API"
          value={inr(s?.api_cost ?? 0)}
          sub={`${s?.api_calls ?? 0} calls · ${inr(s?.api_cost_today ?? 0)} today`}
          icon={Cpu}
          tone="bad"
        />
        <StatTile
          label="Subscriptions"
          value={inr(s?.subscriptions ?? 0)}
          sub="Zerodha Kite Connect ₹500/mo"
          icon={Wallet}
          tone="bad"
        />
        <StatTile
          label="Total Expenses"
          value={inr(s?.total_expenses ?? 0)}
          sub={`USD→INR @ ${s?.usd_inr_rate ?? '—'}`}
          icon={TrendingDown}
          tone="bad"
        />
      </div>

      {/* Daily cost bars */}
      <div className="card">
        <div className="card-header">
          <span className="text-sm font-semibold text-gray-300">Daily Cost (last 14 days)</span>
          <span className="text-xs text-gray-600">API + trading charges</span>
        </div>
        <div className="card-body space-y-2">
          {(daily?.days ?? []).length === 0 && (
            <div className="text-xs text-gray-600">No costs recorded yet.</div>
          )}
          {(daily?.days ?? []).map((d: any) => {
            const total = (d.api_cost ?? 0) + (d.trading_charges ?? 0)
            return (
              <div key={d.day} className="flex items-center gap-3 text-xs">
                <div className="w-20 text-gray-500 tabular-nums">{d.day}</div>
                <div className="flex-1 h-4 bg-gray-800/60 rounded overflow-hidden flex">
                  <div
                    className="bg-violet-500/70 h-full"
                    style={{ width: `${((d.api_cost ?? 0) / maxDay) * 100}%` }}
                    title={`API ${inr(d.api_cost)}`}
                  />
                  <div
                    className="bg-amber-500/70 h-full"
                    style={{ width: `${((d.trading_charges ?? 0) / maxDay) * 100}%` }}
                    title={`Charges ${inr(d.trading_charges)}`}
                  />
                </div>
                <div className="w-24 text-right text-gray-400 tabular-nums">{inr(total)}</div>
                <div className="w-16 text-right text-gray-600">{d.trades ?? 0} trd</div>
              </div>
            )
          })}
          <div className="flex gap-4 pt-1 text-xs text-gray-600">
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm bg-violet-500/70" /> Claude API
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm bg-amber-500/70" /> Trading charges
            </span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent API calls */}
        <div className="card">
          <div className="card-header">
            <span className="text-sm font-semibold text-gray-300">Recent AI Calls</span>
            <span className="text-xs text-gray-600">
              {(s?.api_tokens?.cache_read ?? 0).toLocaleString()} cached tokens saved
            </span>
          </div>
          <div className="card-body max-h-80 overflow-y-auto space-y-1.5">
            {(usage?.calls ?? []).length === 0 && (
              <div className="text-xs text-gray-600">No API calls recorded yet.</div>
            )}
            {(usage?.calls ?? []).map((c: any) => (
              <div key={c.id} className="flex items-center justify-between text-xs border-b border-gray-800/60 pb-1.5">
                <div className="min-w-0">
                  <div className="text-gray-300 truncate">
                    {c.model.replace('claude-', '')} · {c.purpose}
                    {c.cycle_number ? ` · cycle ${c.cycle_number}` : ''}
                  </div>
                  <div className="text-gray-600 tabular-nums">
                    in {c.input_tokens.toLocaleString()} · out {c.output_tokens.toLocaleString()} · cache{' '}
                    {c.cache_read_tokens.toLocaleString()}
                  </div>
                </div>
                <div className="text-gray-400 tabular-nums ml-3">{inr(c.cost_inr)}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Subscriptions + manual entry */}
        <div className="card">
          <div className="card-header">
            <span className="text-sm font-semibold text-gray-300">Subscriptions &amp; Manual Expenses</span>
          </div>
          <div className="card-body space-y-3">
            <div className="space-y-1.5 max-h-44 overflow-y-auto">
              {(list?.expenses ?? []).length === 0 && (
                <div className="text-xs text-gray-600">Nothing recorded yet.</div>
              )}
              {(list?.expenses ?? []).map((e: any) => (
                <div key={e.id} className="flex items-center justify-between text-xs">
                  <span className="text-gray-400">
                    {e.label}
                    <span className="text-gray-600 ml-1.5">
                      ({e.category}
                      {e.period && e.period.length === 7 ? ` · ${e.period}` : ''})
                    </span>
                  </span>
                  <span className="text-gray-300 tabular-nums">{inr(e.amount_inr)}</span>
                </div>
              ))}
            </div>

            <div className="border-t border-gray-800 pt-3">
              <div className="text-xs text-gray-500 mb-2">
                Add an expense (e.g. a Claude credit top-up you purchased)
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={form.label}
                  placeholder="Claude API credits"
                  onChange={(e) => setForm({ ...form, label: e.target.value })}
                  className="flex-1 bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-violet-500"
                />
                <input
                  type="number"
                  value={form.amount_inr || ''}
                  placeholder="2000"
                  onChange={(e) => setForm({ ...form, amount_inr: Number(e.target.value) })}
                  className="w-24 bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-violet-500"
                />
                <button
                  onClick={() => form.label && form.amount_inr > 0 && mutation.mutate(form)}
                  disabled={mutation.isPending || !form.label || form.amount_inr <= 0}
                  className="px-3 py-1.5 rounded bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-xs text-white flex items-center gap-1"
                >
                  <Plus size={12} /> Add
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="text-xs text-gray-600 flex items-center gap-1.5">
        <TrendingUp size={12} />
        Trading charges are modelled per Zerodha CNC rates (STT 0.1%/side, stamp 0.015% buy, exchange +
        SEBI + 18% GST, ₹15.34 DP per sell). API cost uses Claude list pricing converted at the rate
        shown — set AAITRADE_USD_INR in .env to adjust.
      </div>
    </div>
  )
}
