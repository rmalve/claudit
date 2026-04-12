import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import { useInfiniteScroll } from '../hooks/useInfiniteScroll'
import { formatTimestamp } from '../utils/time'
import SessionTree from '../components/SessionTree'

export default function Sessions() {
  const [selectedSession, setSelectedSession] = useState(null)
  const [viewMode, setViewMode] = useState('tree') // 'tree' or 'flat'
  const { items: sessions, loading, hasMore, sentinelRef } = useInfiniteScroll(
    '/api/sessions',
    { limit: 20, itemsKey: 'sessions' }
  )

  // Hierarchy view (lazy: only fetched when session selected)
  const { data: hierarchy, loading: hierarchyLoading } = useApi(
    selectedSession ? `/api/sessions/${selectedSession}/hierarchy` : null,
    { enabled: !!selectedSession && viewMode === 'tree' }
  )

  // Flat view fallback
  const { data: toolCalls } = useApi(
    selectedSession ? `/api/tool-calls?session_id=${selectedSession}&limit=100` : null,
    { enabled: !!selectedSession && viewMode === 'flat' }
  )

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-bold text-brand-text">Sessions</h1>

      {loading && sessions.length === 0 && <div className="text-brand-text-tertiary">Loading...</div>}

      <div className="grid grid-cols-3 gap-4">
        {/* Session list — left panel, infinite scroll */}
        <div className="col-span-1 space-y-2 max-h-[calc(100vh-8rem)] overflow-y-auto">
          {sessions.map((s, i) => {
            const p = s.payload || {}
            const sid = p.session_id || '—'
            const isSelected = selectedSession === sid

            return (
              <div
                key={sid + i}
                className={`bg-brand-surface border rounded-lg p-3 cursor-pointer transition-colors ${
                  isSelected ? 'border-brand-accent' : 'border-brand-border hover:border-brand-accent/50'
                }`}
                onClick={() => setSelectedSession(isSelected ? null : sid)}
              >
                <div className="text-sm font-mono text-brand-text">{sid.slice(0, 12)}...</div>
                <div className="flex gap-3 mt-1 text-xs text-brand-text-tertiary">
                  <span>{p.total_tool_calls || 0} calls</span>
                  <span>{(p.duration_seconds || 0).toFixed(0)}s</span>
                  <span>{p.project || '—'}</span>
                </div>
                {(p.tool_failures > 0 || p.hallucinations_detected > 0) && (
                  <div className="flex gap-2 mt-1">
                    {p.tool_failures > 0 && (
                      <span className="text-xs text-brand-red">{p.tool_failures} failures</span>
                    )}
                    {p.hallucinations_detected > 0 && (
                      <span className="text-xs text-brand-accent">{p.hallucinations_detected} hallucinations</span>
                    )}
                  </div>
                )}
              </div>
            )
          })}

          {/* Infinite scroll sentinel */}
          <div ref={sentinelRef} className="h-1" />
          {loading && sessions.length > 0 && (
            <div className="text-brand-text-tertiary text-center text-xs py-2">Loading more...</div>
          )}
          {!hasMore && sessions.length > 0 && (
            <div className="text-brand-text-tertiary text-center text-xs py-2">{sessions.length} sessions loaded</div>
          )}

          {!loading && sessions.length === 0 && (
            <div className="text-brand-text-tertiary text-center py-8">No sessions found.</div>
          )}
        </div>

        {/* Detail panel — right */}
        <div className="col-span-2">
          {selectedSession ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold text-brand-text">
                  Session: {selectedSession.slice(0, 12)}...
                </h2>
                <div className="flex gap-1 bg-brand-bg-secondary rounded-lg p-0.5">
                  <button
                    onClick={() => setViewMode('tree')}
                    className={`text-xs px-3 py-1 rounded-md transition-colors ${
                      viewMode === 'tree'
                        ? 'bg-brand-surface text-brand-text shadow-sm'
                        : 'text-brand-text-tertiary hover:text-brand-text'
                    }`}
                  >
                    Tree
                  </button>
                  <button
                    onClick={() => setViewMode('flat')}
                    className={`text-xs px-3 py-1 rounded-md transition-colors ${
                      viewMode === 'flat'
                        ? 'bg-brand-surface text-brand-text shadow-sm'
                        : 'text-brand-text-tertiary hover:text-brand-text'
                    }`}
                  >
                    Flat
                  </button>
                </div>
              </div>

              {/* Tree view */}
              {viewMode === 'tree' && (
                <>
                  {hierarchyLoading && (
                    <div className="text-brand-text-tertiary text-sm">Loading session hierarchy...</div>
                  )}
                  {hierarchy && <SessionTree data={hierarchy} />}
                  {hierarchy && hierarchy.prompt_turns?.length === 0 && (
                    <div className="text-brand-text-tertiary text-center py-4">No events found for this session.</div>
                  )}
                </>
              )}

              {/* Flat view fallback */}
              {viewMode === 'flat' && (
                <>
                  {(toolCalls?.tool_calls || []).map((tc, i) => {
                    const p = tc.payload || {}
                    return (
                      <div key={i} className="bg-brand-surface border border-brand-border rounded-lg p-3">
                        <div className="flex items-center gap-3">
                          <span className={`text-xs font-mono px-2 py-0.5 rounded-md ${
                            p.status === 'success' ? 'bg-brand-green/10 text-brand-green' : 'bg-brand-red/10 text-brand-red'
                          }`}>
                            {p.tool_name || '?'}
                          </span>
                          <span className="text-xs text-brand-text-tertiary">{formatTimestamp(p.timestamp)}</span>
                          <span className="text-xs text-brand-text-tertiary">{p.agent || ''}</span>
                        </div>
                        {tc.text && (
                          <pre className="text-xs text-brand-text-secondary mt-2 overflow-x-auto whitespace-pre-wrap font-mono">
                            {tc.text.slice(0, 300)}
                          </pre>
                        )}
                      </div>
                    )
                  })}
                  {toolCalls?.count === 0 && (
                    <div className="text-brand-text-tertiary text-center py-4">No tool calls found for this session.</div>
                  )}
                </>
              )}
            </div>
          ) : (
            <div className="text-brand-text-tertiary text-center py-16">
              Select a session to view details.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
