import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import { formatTimestamp } from '../utils/time'
import SeverityBadge from '../components/SeverityBadge'
import FilterBar from '../components/FilterBar'

const severityOptions = ['critical', 'high', 'medium', 'low', 'info']
const auditorOptions = ['trace', 'safety', 'policy', 'hallucination', 'drift', 'cost']
const typeOptions = ['violation', 'anomaly', 'trend', 'info']

export default function Findings() {
  const [searchParams] = useSearchParams()
  const [filters, setFilters] = useState(() => {
    const initial = {}
    if (searchParams.get('severity')) initial.severity = searchParams.get('severity')
    if (searchParams.get('auditor_type')) initial.auditor_type = searchParams.get('auditor_type')
    if (searchParams.get('finding_type')) initial.finding_type = searchParams.get('finding_type')
    return initial
  })
  const [selected, setSelected] = useState(null)

  const params = new URLSearchParams()
  if (filters.severity) params.set('severity', filters.severity)
  if (filters.auditor_type) params.set('auditor_type', filters.auditor_type)
  if (filters.finding_type) params.set('finding_type', filters.finding_type)
  params.set('limit', '100')

  const { data, loading } = useApi(`/api/findings?${params}`, { refreshInterval: 15000 })

  const clusterIds = searchParams.get('cluster_ids')
  const clusterIdSet = clusterIds ? new Set(clusterIds.split(',')) : null
  const findings = clusterIdSet
    ? (data?.findings || []).filter(f => clusterIdSet.has(f.finding_id))
    : (data?.findings || [])

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-brand-text">Findings</h1>
        <span className="text-sm text-brand-text-tertiary">{findings.length} results</span>
      </div>

      <FilterBar
        filters={[
          { key: 'severity', label: 'Severity', options: severityOptions },
          { key: 'auditor_type', label: 'Auditor', options: auditorOptions },
          { key: 'finding_type', label: 'Type', options: typeOptions },
        ]}
        values={filters}
        onChange={setFilters}
      />

      {loading && <div className="text-brand-text-tertiary">Loading...</div>}

      <div className="space-y-2">
        {findings.map((f, i) => (
          <div
            key={f.finding_id || f.stream_id || i}
            className={`bg-brand-surface border rounded-lg p-4 cursor-pointer transition-colors ${
              selected === i ? 'border-brand-accent' : 'border-brand-border hover:border-brand-accent/50'
            }`}
            onClick={() => setSelected(selected === i ? null : i)}
          >
            <div className="flex items-start gap-3">
              <SeverityBadge severity={f.severity} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-brand-text truncate">
                    {f.title || f.claim || '(no title)'}
                  </span>
                </div>
                <div className="flex gap-3 mt-1 text-xs text-brand-text-tertiary">
                  <span>Auditor: {f.auditor_type || f.auditor || 'director'}</span>
                  <span>Confidence: {f.confidence != null ? Number(f.confidence).toFixed(2) : '—'}</span>
                  <span>Project: {f.project || '—'}</span>
                  <span>Session: {(f.target_session || '—').slice(0, 8)}</span>
                </div>
              </div>
              <span className="text-xs text-brand-text-tertiary whitespace-nowrap">
                {f.finding_id?.slice(0, 8) || ''}
              </span>
            </div>

            {selected === i && (
              <div className="mt-4 space-y-3 border-t border-brand-border pt-3">
                {(f.detail || f.evidence) && (
                  <div>
                    <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Evidence</div>
                    <pre className="text-xs text-brand-text-secondary bg-brand-bg-secondary rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
                      {typeof (f.detail || f.evidence) === 'string'
                        ? (f.detail || f.evidence)
                        : JSON.stringify(f.detail || f.evidence, null, 2)}
                    </pre>
                  </div>
                )}
                {(f.recommendation || f.recommended_action) && (
                  <div>
                    <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Recommendation</div>
                    <p className="text-sm text-brand-text-secondary">{f.recommendation || f.recommended_action}</p>
                  </div>
                )}
                {f.target_event_ids?.length > 0 && (
                  <div>
                    <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Event IDs</div>
                    <div className="flex flex-wrap gap-1">
                      {f.target_event_ids.map((id, j) => (
                        <code key={j} className="text-xs font-mono bg-brand-bg-secondary px-2 py-0.5 rounded text-brand-text-tertiary">
                          {typeof id === 'string' ? id.slice(0, 20) : JSON.stringify(id).slice(0, 20)}
                        </code>
                      ))}
                    </div>
                  </div>
                )}
                <div className="text-xs text-brand-text-tertiary">
                  Finding ID: {f.finding_id || '—'} | Stream ID: {f.stream_id || '—'} | {formatTimestamp(f.timestamp)}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
