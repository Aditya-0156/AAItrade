import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BookOpen, ChevronDown, ChevronUp, Brain, Clock, Tag, Target, Shield } from 'lucide-react'
import { fetchSessions, fetchJournal, fetchJournalUpdates, fetchMemory } from '../api'
import { StatusBadge, ModeBadge } from '../components/shared/StatusBadge'
import { PnLBadge } from '../components/shared/PnLBadge'
import type { JournalEntry, ThesisUpdate } from '../types'

function toIST(str: string | null | undefined) {
  if (!str) return '—'
  try {
    return new Date(str).toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return str
  }
}

function parseJsonArray(raw: string | null | undefined): string[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return raw ? [raw] : []
  }
}

function JournalUpdatesTimeline({
  journalId,
}: {
  journalId: number
}) {
  const { data: updates = [], isLoading } = useQuery({
    queryKey: ['journal_updates', journalId],
    queryFn: () => fetchJournalUpdates(journalId),
  })

  if (isLoading) {
    return (
      <div className="mt-3 space-y-2">
        <div className="skeleton h-10 w-full rounded" />
      </div>
    )
  }

  if (updates.length === 0) {
    return (
      <div className="mt-3 text-xs text-gray-600 italic">No thesis updates recorded.</div>
    )
  }

  return (
    <div className="mt-3 relative">
      <div className="absolute left-2 top-0 bottom-0 w-px bg-gray-800" />
      {updates.map((u: ThesisUpdate) => (
        <div key={u.id} className="relative pl-7 pb-4">
          <div className="absolute left-0.5 top-1.5 w-3 h-3 rounded-full bg-gray-700 border-2 border-gray-600" />
          <div className="text-xs text-gray-500 mb-0.5">{toIST(u.updated_at)}</div>
          <p className="text-sm text-gray-300 leading-relaxed">{u.note}</p>
        </div>
      ))}
    </div>
  )
}

function JournalCard({ entry }: { entry: JournalEntry }) {
  const [expanded, setExpanded] = useState(false)
  const newsCited = parseJsonArray(entry.news_cited)

  const statusColor =
    entry.status === 'open'
      ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
      : entry.status === 'closed'
      ? 'bg-blue-500/15 text-blue-400 border-blue-500/30'
      : 'bg-red-500/15 text-red-400 border-red-500/30'

  return (
    <div className="card overflow-hidden">
      {/* Card header */}
      <div
        className="card-header cursor-pointer select-none"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex items-start gap-3 min-w-0">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono font-bold text-gray-100 text-base">{entry.symbol}</span>
              <span className={`badge border ${statusColor}`}>
                {entry.status.toUpperCase()}
              </span>
              {entry.pnl != null && (
                <PnLBadge value={entry.pnl} className="text-sm" />
              )}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              Opened {toIST(entry.opened_at)}
              {entry.closed_at && ` · Closed ${toIST(entry.closed_at)}`}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 text-gray-600">
          {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </div>
      </div>

      {/* Entry & Stop/Target quick stats */}
      <div className="px-5 py-3 flex flex-wrap gap-6 border-b border-gray-800/50 bg-gray-900/50">
        <div>
          <div className="text-xs text-gray-600 mb-0.5">Entry</div>
          <div className="font-mono text-sm text-gray-200">
            ₹{entry.entry_price?.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
          </div>
        </div>
        {entry.target_price && (
          <div>
            <div className="text-xs text-gray-600 mb-0.5 flex items-center gap-1">
              <Target size={9} /> Target
            </div>
            <div className="font-mono text-sm text-emerald-400">
              ₹{entry.target_price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
            </div>
          </div>
        )}
        {entry.stop_price && (
          <div>
            <div className="text-xs text-gray-600 mb-0.5 flex items-center gap-1">
              <Shield size={9} /> Stop
            </div>
            <div className="font-mono text-sm text-red-400">
              ₹{entry.stop_price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
            </div>
          </div>
        )}
        {entry.exit_price && (
          <div>
            <div className="text-xs text-gray-600 mb-0.5">Exit</div>
            <div className="font-mono text-sm text-gray-300">
              ₹{entry.exit_price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
            </div>
          </div>
        )}
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="card-body space-y-4">
          {/* Key thesis */}
          {entry.key_thesis && (
            <div>
              <div className="text-xs text-gray-500 mb-1.5 flex items-center gap-1 uppercase tracking-wider">
                <Brain size={11} /> Key Thesis
              </div>
              <p className="text-sm text-gray-200 leading-relaxed">{entry.key_thesis}</p>
            </div>
          )}

          {/* Reason */}
          {entry.reason && (
            <div>
              <div className="text-xs text-gray-500 mb-1.5 uppercase tracking-wider">Entry Reason</div>
              <p className="text-sm text-gray-400 leading-relaxed">{entry.reason}</p>
            </div>
          )}

          {/* News cited */}
          {newsCited.length > 0 && (
            <div>
              <div className="text-xs text-gray-500 mb-1.5 flex items-center gap-1 uppercase tracking-wider">
                <Tag size={11} /> News Cited
              </div>
              <ul className="space-y-1">
                {newsCited.map((news, i) => (
                  <li key={i} className="text-xs text-gray-400 bg-gray-800 px-3 py-1.5 rounded">
                    {news}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Exit reason */}
          {entry.exit_reason && (
            <div>
              <div className="text-xs text-gray-500 mb-1.5 uppercase tracking-wider">Exit Reason</div>
              <p className="text-sm text-gray-400 leading-relaxed">{entry.exit_reason}</p>
            </div>
          )}

          {/* Thesis updates timeline */}
          <div>
            <div className="text-xs text-gray-500 mb-2 flex items-center gap-1 uppercase tracking-wider">
              <Clock size={11} /> Thesis Updates
            </div>
            <JournalUpdatesTimeline journalId={entry.id} />
          </div>
        </div>
      )}
    </div>
  )
}

function MemoryViewer({ sessionId }: { sessionId: number }) {
  const { data: memory, isLoading } = useQuery({
    queryKey: ['memory', sessionId],
    queryFn: () => fetchMemory(sessionId),
    refetchInterval: 60_000,
  })

  if (isLoading) {
    return (
      <div className="card">
        <div className="card-header">
          <span className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <Brain size={14} /> Session Memory
          </span>
        </div>
        <div className="card-body">
          <div className="skeleton h-32 w-full rounded" />
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <Brain size={14} className="text-gray-500" />
          <span className="text-sm font-semibold text-gray-300">Session Memory</span>
        </div>
        {memory?.updated_at && (
          <span className="text-xs text-gray-600">
            Updated {toIST(memory.updated_at)} · Cycle {memory.cycle_number}
          </span>
        )}
      </div>
      <div className="card-body">
        {memory?.content ? (
          <pre className="text-xs text-gray-400 whitespace-pre-wrap leading-relaxed font-mono bg-gray-950 p-4 rounded-lg border border-gray-800 max-h-96 overflow-y-auto">
            {memory.content}
          </pre>
        ) : (
          <div className="py-8 text-center text-gray-600 text-sm">No memory stored yet.</div>
        )}
      </div>
    </div>
  )
}

export function DeepDive() {
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const { data: sessions = [], isLoading: loadingSessions } = useQuery({
    queryKey: ['sessions'],
    queryFn: fetchSessions,
  })

  const { data: journal = [], isLoading: loadingJournal } = useQuery({
    queryKey: ['journal', selectedId],
    queryFn: () => fetchJournal(selectedId ?? undefined),
    enabled: selectedId !== null,
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
            <div className="skeleton h-10 w-full max-w-xs rounded-lg" />
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
          {/* Session memory */}
          <MemoryViewer sessionId={selectedSession.id} />

          {/* Trade journal */}
          <div>
            <div className="flex items-center gap-2 mb-4">
              <BookOpen size={14} className="text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
                Trade Journal
              </h2>
              <span className="text-xs text-gray-600">{journal.length} entries</span>
            </div>

            {loadingJournal ? (
              <div className="space-y-3">
                {[1, 2].map((i) => (
                  <div key={i} className="skeleton h-24 w-full rounded-xl" />
                ))}
              </div>
            ) : journal.length === 0 ? (
              <div className="card py-16 text-center text-gray-600 text-sm">
                No journal entries for this session.
              </div>
            ) : (
              <div className="space-y-3">
                {journal.map((entry) => (
                  <JournalCard key={entry.id} entry={entry} />
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {!selectedSession && !loadingSessions && sessions.length > 0 && (
        <div className="text-center py-16 text-gray-600 text-sm">
          Select a session above to explore the trade journal and session memory.
        </div>
      )}
    </div>
  )
}
