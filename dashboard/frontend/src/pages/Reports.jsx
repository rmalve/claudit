import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import { formatTimestamp } from '../utils/time'
import SeverityBadge from '../components/SeverityBadge'

const riskColors = {
  LOW: 'bg-brand-green/15 text-brand-green border-brand-green/30',
  MEDIUM: 'bg-[#C4A95B]/15 text-[#C4A95B] border-[#C4A95B]/30',
  HIGH: 'bg-brand-accent/15 text-brand-accent border-brand-accent/30',
  CRITICAL: 'bg-brand-red/15 text-brand-red border-brand-red/30',
}

const auditorStyles = {
  trace: { color: 'text-[#3B82D9]', bg: 'bg-[#3B82D9]/10' },
  safety: { color: 'text-[#4A8C6F]', bg: 'bg-[#4A8C6F]/10' },
  policy: { color: 'text-[#8B6AAE]', bg: 'bg-[#8B6AAE]/10' },
  hallucination: { color: 'text-[#A0527A]', bg: 'bg-[#A0527A]/10' },
  drift: { color: 'text-[#6B8F3C]', bg: 'bg-[#6B8F3C]/10' },
  cost: { color: 'text-[#C4A95B]', bg: 'bg-[#C4A95B]/10' },
  director: { color: 'text-[#7A7A72]', bg: 'bg-[#7A7A72]/10' },
}

function safeParse(val) {
  if (!val) return val
  if (typeof val === 'string') {
    try { return JSON.parse(val) } catch { return val }
  }
  return val
}

function FullReport({ report }) {
  const payload = report.payload ? safeParse(report.payload) : report

  return (
    <div className="mt-3 border-t border-brand-border pt-3">
      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-2">Raw Report Data</div>
      <pre className="text-xs text-brand-text-secondary bg-brand-bg-secondary rounded-md p-3 overflow-x-auto whitespace-pre-wrap max-h-96 overflow-y-auto font-mono">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </div>
  )
}

export default function Reports() {
  const [selectedReport, setSelectedReport] = useState(null)
  const [showFullReport, setShowFullReport] = useState(null)
  const [openAuditors, setOpenAuditors] = useState({})

  const { data, loading } = useApi('/api/reports', { refreshInterval: 15000 })

  const reports = data?.reports || []

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-brand-text">Audit Reports</h1>
        <span className="text-sm text-brand-text-tertiary">{reports.length} reports</span>
      </div>

      {loading && <div className="text-brand-text-tertiary">Loading...</div>}

      <div className="space-y-2">
        {reports.map((r, i) => {
          const risk = (r.overall_risk || '').toUpperCase()
          const riskCls = riskColors[risk] || 'bg-brand-bg-tertiary text-brand-text-tertiary border-brand-border'
          const parsedCounts = safeParse(r.findings_count) || {}
          const parsedSessions = safeParse(r.sessions_audited) || []
          const parsedAuditors = safeParse(r.auditor_status) || {}
          const isSelected = selectedReport === i

          return (
            <div
              key={r.report_id || i}
              className={`bg-brand-surface border rounded-lg transition-colors ${
                isSelected ? 'border-brand-accent' : 'border-brand-border'
              }`}
            >
              <div
                className="p-4 cursor-pointer hover:bg-brand-bg-secondary/50 transition-colors rounded-lg"
                onClick={() => {
                  setSelectedReport(isSelected ? null : i)
                  setOpenAuditors({})
                  setShowFullReport(null)
                }}
              >
                <div className="flex items-start gap-3">
                  <span className={`text-[11px] font-bold uppercase px-2.5 py-0.5 rounded-full shrink-0 border ${riskCls}`}>
                    {risk || '?'} RISK
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-brand-text">
                        {r.report_id || r.audit_cycle || '(no ID)'}
                      </span>
                    </div>
                    <div className="flex gap-3 mt-1 text-xs text-brand-text-tertiary">
                      <span>Project: {r.project || '—'}</span>
                      <span>Date: {r.date || r.timestamp?.split('T')[0] || '—'}</span>
                      <span>Directives: {r.directives_issued ?? '—'}</span>
                      {r.infrastructure_escalations != null && (
                        <span>Escalations: {r.infrastructure_escalations}</span>
                      )}
                    </div>
                    <div className="flex gap-2 mt-1.5">
                      {Object.entries(parsedCounts).map(([sev, count]) => (
                        <span key={sev} className="text-xs">
                          <SeverityBadge severity={sev} />
                          <span className="text-brand-text-tertiary ml-0.5">{count}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {isSelected && (
                <div className="px-4 pb-4 space-y-4">
                  <div className="border-t border-brand-border pt-3" />

                  {(r.full_report || (r.payload && safeParse(r.payload)?.full_report)) ? (
                    <div>
                      <pre className="text-sm text-brand-text-secondary bg-brand-bg-secondary rounded-lg p-5 overflow-x-auto whitespace-pre-wrap leading-relaxed max-h-[80vh] overflow-y-auto font-serif">
                        {r.full_report || safeParse(r.payload)?.full_report}
                      </pre>
                    </div>
                  ) : (
                    <>
                      {r.summary && (
                        <div>
                          <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Director's Summary</div>
                          <p className="text-sm text-brand-text-secondary bg-brand-bg-secondary rounded-md p-3">{r.summary}</p>
                        </div>
                      )}

                      {parsedSessions.length > 0 && (
                        <div>
                          <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Sessions Audited</div>
                          <div className="flex flex-wrap gap-1">
                            {parsedSessions.map((sid, j) => (
                              <code key={j} className="text-xs font-mono bg-brand-bg-secondary px-2 py-0.5 rounded text-brand-text-tertiary">
                                {typeof sid === 'string' ? sid.slice(0, 16) : sid}...
                              </code>
                            ))}
                          </div>
                        </div>
                      )}

                      {Object.keys(parsedAuditors).length > 0 && (
                        <div>
                          <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Auditor Status</div>
                          <div className="grid grid-cols-3 gap-2">
                            {Object.entries(parsedAuditors).map(([auditor, status]) => {
                              const styles = auditorStyles[auditor] || auditorStyles.director
                              return (
                                <div key={auditor} className={`rounded-md p-2 ${styles.bg}`}>
                                  <div className={`text-xs font-medium capitalize ${styles.color}`}>{auditor}</div>
                                  <div className="text-xs text-brand-text-tertiary">{status}</div>
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )}

                      {r.open_findings_from_prior_cycles && (
                        <div>
                          <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Open Findings from Prior Cycles</div>
                          <pre className="text-xs text-brand-text-secondary bg-brand-bg-secondary rounded-md p-3 overflow-x-auto font-mono">
                            {JSON.stringify(safeParse(r.open_findings_from_prior_cycles), null, 2)}
                          </pre>
                        </div>
                      )}
                    </>
                  )}

                  <div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setShowFullReport(showFullReport === i ? null : i)
                      }}
                      className="text-xs text-brand-accent hover:text-brand-accent-dark underline"
                    >
                      {showFullReport === i ? 'Hide raw report data' : 'Show raw report data'}
                    </button>
                    {showFullReport === i && <FullReport report={r} />}
                  </div>

                  <div className="text-xs text-brand-text-tertiary">
                    Report ID: {r.report_id || '—'} | Cycle: {r.audit_cycle || r.audit_cycle_id || '—'} | {formatTimestamp(r.timestamp)}
                  </div>
                </div>
              )}
            </div>
          )
        })}

        {!loading && reports.length === 0 && (
          <div className="text-brand-text-tertiary text-center py-8">No audit reports yet.</div>
        )}
      </div>
    </div>
  )
}
