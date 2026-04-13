import { useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import { formatTimestamp } from '../utils/time'
import FilterBar from '../components/FilterBar'
import ConfirmModal from '../components/ConfirmModal'
import DirectiveLifecycleTimeline from '../components/DirectiveLifecycleTimeline'
import CyclesToVerificationChart from '../components/CyclesToVerificationChart'
import NonVerifiedCounterStrip from '../components/NonVerifiedCounterStrip'

const statusColors = {
  PENDING: { bg: 'bg-[#C4A95B]/15', text: 'text-[#C4A95B]', label: 'Pending' },
  ACKNOWLEDGED: { bg: 'bg-brand-blue/15', text: 'text-brand-blue', label: 'Acknowledged' },
  VERIFICATION_PENDING: { bg: 'bg-[#8B6AAE]/15', text: 'text-[#8B6AAE]', label: 'Verifying' },
  VERIFIED_COMPLIANT: { bg: 'bg-brand-green/15', text: 'text-brand-green', label: 'Verified Compliant' },
  VERIFIED_NON_COMPLIANT: { bg: 'bg-brand-red/15', text: 'text-brand-red', label: 'Non-Compliant (Verified)' },
  NON_COMPLIANT: { bg: 'bg-brand-red/20', text: 'text-brand-red', label: 'Non-Compliant' },
  ESCALATED: { bg: 'bg-brand-accent/15', text: 'text-brand-accent', label: 'Escalated' },
  SUPERSEDED: { bg: 'bg-brand-bg-tertiary', text: 'text-brand-text-tertiary', label: 'Superseded' },
  DISMISSED: { bg: 'bg-brand-bg-tertiary', text: 'text-brand-text-tertiary', label: 'Dismissed' },
}

const typeOptions = ['RECOMMENDATION', 'DIRECTIVE']
const statusOptions = Object.keys(statusColors)


function ComplianceTimeline({ events }) {
  if (!events || events.length === 0) return null

  return (
    <div className="mt-3">
      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-2">Compliance Timeline</div>
      <div className="border-l-2 border-brand-border ml-2 space-y-3">
        {events.map((evt, i) => {
          const isVerification = evt.is_verification || evt.is_verification === 1
          const hasConflict = !!evt.conflict_reason
          const verificationPassed = evt.verification_passed === true || evt.verification_passed === 1

          return (
            <div key={i} className="ml-4 relative">
              <div className={`absolute -left-[21px] w-3 h-3 rounded-full border-2 ${
                isVerification
                  ? (verificationPassed ? 'bg-brand-green border-brand-green' : 'bg-brand-red border-brand-red')
                  : hasConflict
                    ? 'bg-brand-accent border-brand-accent'
                    : 'bg-brand-blue border-brand-blue'
              }`} />

              <div className="bg-brand-bg-secondary rounded-md p-2">
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-semibold ${
                    isVerification
                      ? (verificationPassed ? 'text-brand-green' : 'text-brand-red')
                      : hasConflict ? 'text-brand-accent' : 'text-brand-blue'
                  }`}>
                    {isVerification
                      ? (verificationPassed ? 'Verified Compliant' : 'Verified Non-Compliant')
                      : hasConflict ? 'Conflict Escalated' : 'Acknowledged'}
                  </span>
                  <span className="text-[10px] text-brand-text-tertiary">
                    {formatTimestamp(evt.timestamp)}
                  </span>
                </div>

                <div className="text-xs text-brand-text-tertiary mt-1">
                  Agent: <span className="text-brand-text-secondary">{evt.agent || '—'}</span>
                  {evt.agent_version && <span className="text-brand-text-tertiary ml-2">v{evt.agent_version}</span>}
                </div>

                {evt.action_taken && (
                  <div className="text-xs text-brand-text-secondary mt-1">{evt.action_taken}</div>
                )}

                {evt.conflict_reason && (
                  <div className="text-xs text-brand-accent mt-1 bg-brand-accent/10 rounded p-1.5">
                    Conflict: {evt.conflict_reason}
                  </div>
                )}

                {isVerification && (
                  <div className="mt-1 space-y-1">
                    {evt.sessions_examined != null && (
                      <div className="text-xs text-brand-text-tertiary">
                        Sessions examined: {evt.sessions_examined}
                      </div>
                    )}
                    {evt.verification_evidence && (
                      <pre className="text-xs text-brand-text-secondary bg-brand-bg rounded-md p-2 overflow-x-auto whitespace-pre-wrap">
                        {evt.verification_evidence}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const promotionStatusColors = {
  PENDING_ACK: { bg: 'bg-[#C4A95B]/15', text: 'text-[#C4A95B]', label: 'Pending Ack' },
  VERIFIED: { bg: 'bg-brand-green/15', text: 'text-brand-green', label: 'Verified' },
  ESCALATED: { bg: 'bg-brand-red/15', text: 'text-brand-red', label: 'Escalated' },
  DECLINED: { bg: 'bg-brand-bg-tertiary', text: 'text-brand-text-tertiary', label: 'Declined' },
}

function PromotionDecisions({ decisions }) {
  const [expanded, setExpanded] = useState(null)

  return (
    <div className="mt-3">
      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-2">Promotion Decisions</div>
      <div className="space-y-2">
        {decisions.map((p, i) => {
          const pStatus = promotionStatusColors[p.status] || promotionStatusColors.PENDING_ACK
          const isExpanded = expanded === i

          return (
            <div key={p.promotion_id || i} className="bg-brand-bg-secondary rounded-md border border-brand-border">
              <div
                className="p-2 cursor-pointer flex items-center justify-between"
                onClick={() => setExpanded(isExpanded ? null : i)}
              >
                <div className="flex items-center gap-2">
                  <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold ${pStatus.bg} ${pStatus.text}`}>
                    {pStatus.label}
                  </span>
                  <span className="text-xs text-brand-text-secondary">
                    {p.decision_type?.replace('_', ' ')} — {p.promotion_id}
                  </span>
                  {p.status === 'VERIFIED' && (
                    <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-brand-green/20 text-brand-green">
                      STANDING
                    </span>
                  )}
                </div>
                <span className="text-brand-text-tertiary text-xs">{isExpanded ? '▲' : '▼'}</span>
              </div>

              {isExpanded && (
                <div className="p-3 border-t border-brand-border space-y-2 text-xs">
                  <div>
                    <span className="text-brand-text-tertiary">Rationale: </span>
                    <span className="text-brand-text-secondary">{p.rationale || '—'}</span>
                  </div>
                  {p.classification_reasoning && (
                    <div>
                      <span className="text-brand-text-tertiary">Classification: </span>
                      <span className="text-brand-text-secondary">{p.classification_reasoning}</span>
                    </div>
                  )}
                  {p.supersession_reasoning && (
                    <div>
                      <span className="text-brand-text-tertiary">Supersession: </span>
                      <span className="text-brand-text-secondary">{p.supersession_reasoning}</span>
                    </div>
                  )}
                  {p.alternatives_considered && (
                    <div>
                      <span className="text-brand-text-tertiary">Alternatives: </span>
                      <span className="text-brand-text-secondary">{p.alternatives_considered}</span>
                    </div>
                  )}
                  {p.add_verbiage && (
                    <div>
                      <div className="text-brand-text-tertiary mb-1">Add Verbiage:</div>
                      <pre className="text-brand-text-secondary bg-brand-bg rounded-md p-2 whitespace-pre-wrap">{p.add_verbiage}</pre>
                    </div>
                  )}
                  {p.remove_verbiage && (
                    <div>
                      <div className="text-brand-text-tertiary mb-1">Remove Verbiage:</div>
                      <pre className="text-brand-red/70 bg-brand-bg rounded-md p-2 whitespace-pre-wrap">{p.remove_verbiage}</pre>
                    </div>
                  )}
                  <div className="text-brand-text-tertiary pt-1">
                    Cycle: {p.audit_cycle_id || '—'} | {formatTimestamp(p.timestamp)}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function Directives() {
  const [searchParams] = useSearchParams()
  const [selected, setSelected] = useState(null)
  const [filters, setFilters] = useState(() => {
    const initial = {}
    if (searchParams.get('directive_type') || searchParams.get('type')) {
      initial.directive_type = searchParams.get('directive_type') || searchParams.get('type')
    }
    if (searchParams.get('status')) initial.status = searchParams.get('status')
    if (searchParams.get('project')) initial.project = searchParams.get('project')
    return initial
  })

  const { data: statsData } = useApi('/api/stats')
  const projectOptions = statsData?.active_projects || []

  const params = new URLSearchParams()
  if (filters.directive_type) params.set('directive_type', filters.directive_type)
  if (filters.status) params.set('status', filters.status)
  if (filters.project) params.set('project', filters.project)

  const { data, loading, refetch } = useApi(`/api/directives?${params}`, { refreshInterval: 15000 })
  const [dismissing, setDismissing] = useState(null)
  const [confirmModal, setConfirmModal] = useState({ open: false, directiveId: null, title: '' })

  const directives = (data?.directives || []).filter(d => {
    const status = (d.lifecycle_status || d.status || '').toUpperCase()
    return status !== 'DISMISSED'
  })

  const requestDismiss = useCallback((directiveId, title) => {
    setConfirmModal({
      open: true,
      directiveId,
      title: title || directiveId,
    })
  }, [])

  const executeDismiss = useCallback(async () => {
    const { directiveId } = confirmModal
    setConfirmModal({ open: false, directiveId: null, title: '' })
    setDismissing(directiveId)
    try {
      const res = await fetch(`/api/directives/${directiveId}/dismiss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Dismissed by user via dashboard' }),
      })
      if (!res.ok) throw new Error(`${res.status}`)
      refetch()
      setSelected(null)
    } catch (err) {
      setConfirmModal({
        open: true,
        directiveId: null,
        title: `Failed to dismiss: ${err.message}`,
      })
    } finally {
      setDismissing(null)
    }
  }, [confirmModal, refetch])

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-brand-text">Directives</h1>
        <span className="text-sm text-brand-text-tertiary">{directives.length} total</span>
      </div>

      <FilterBar
        filters={[
          { key: 'project', label: 'Project', options: projectOptions },
          { key: 'directive_type', label: 'Type', options: typeOptions },
          { key: 'status', label: 'Status', options: statusOptions },
        ]}
        values={filters}
        onChange={setFilters}
      />

      {filters.project && (
        <div className="space-y-3">
          <CyclesToVerificationChart project={filters.project} lastNCycles={20} />
          <NonVerifiedCounterStrip project={filters.project} />
        </div>
      )}

      {loading && <div className="text-brand-text-tertiary">Loading...</div>}

      <div className="space-y-2">
        {directives.map((d, i) => {
          const dtype = (d.type || d.directive_type || '?').toUpperCase()
          const lifecycleStatus = d.lifecycle_status || d.status || 'PENDING'
          const complianceEvents = d.compliance_events || []

          return (
            <div
              key={d.directive_id || i}
              className={`bg-brand-surface border rounded-lg p-4 cursor-pointer transition-colors ${
                selected === i ? 'border-brand-accent' : 'border-brand-border hover:border-brand-accent/50'
              }`}
              onClick={() => { if (!window.getSelection()?.toString()) setSelected(selected === i ? null : i) }}
            >
              <div className="flex items-start gap-3">
                <span className={`text-[11px] font-bold uppercase px-2.5 py-0.5 rounded-full shrink-0 ${
                  dtype === 'DIRECTIVE' ? 'bg-brand-accent/15 text-brand-accent' : 'bg-brand-blue/15 text-brand-blue'
                }`}>
                  {dtype}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-brand-text truncate">
                    {d.title || d.content?.slice(0, 80) || '(no title)'}
                  </div>
                  <div className="flex gap-3 mt-1 text-xs text-brand-text-tertiary">
                    <span>Target: {d.target_agent || '—'}</span>
                    <span>Due: {d.compliance_due || '—'}</span>
                    <span>Confidence: {d.confidence != null ? Number(d.confidence).toFixed(2) : '—'}</span>
                    {complianceEvents.length > 0 && (
                      <span className="text-brand-accent">{complianceEvents.length} compliance event(s)</span>
                    )}
                  </div>
                </div>
              </div>

              <DirectiveLifecycleTimeline transitions={d.lifecycle_transitions} lifecycleStatus={lifecycleStatus} />

              {selected === i && (
                <div className="mt-4 space-y-3 border-t border-brand-border pt-3" onClick={e => e.stopPropagation()}>
                  {(d.description || d.content) && (
                    <div>
                      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Content</div>
                      <p className="text-sm text-brand-text-secondary">{d.description || d.content}</p>
                    </div>
                  )}
                  {d.required_action && (
                    <div>
                      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Required Action</div>
                      <p className="text-sm text-brand-text-secondary">{d.required_action}</p>
                    </div>
                  )}
                  {d.verification_criteria && (
                    <div>
                      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Verification Criteria</div>
                      <p className="text-sm text-brand-text-secondary">{d.verification_criteria}</p>
                    </div>
                  )}
                  {(d.metrics || d.supporting_metrics) && (
                    <div>
                      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Supporting Metrics</div>
                      <pre className="text-xs text-brand-text-secondary bg-brand-bg-secondary rounded-md p-3 overflow-x-auto">
                        {JSON.stringify(d.metrics || d.supporting_metrics, null, 2)}
                      </pre>
                    </div>
                  )}

                  <ComplianceTimeline events={complianceEvents} />

                  {complianceEvents.length === 0 && (
                    <div className="text-xs text-[#C4A95B] bg-[#C4A95B]/10 rounded-md p-2">
                      No compliance events yet. Directive is awaiting acknowledgment from the target agent.
                    </div>
                  )}

                  {d.promotion_decisions && d.promotion_decisions.length > 0 && (
                    <PromotionDecisions decisions={d.promotion_decisions} />
                  )}

                  {d.supersedes && (
                    <div className="text-xs text-brand-text-tertiary">Supersedes: {d.supersedes}</div>
                  )}
                  <div className="flex items-center justify-between">
                    <div className="text-xs text-brand-text-tertiary">
                      ID: {d.directive_id || '—'} | Finding: {d.finding_ref || d.triggered_by_finding || '—'} | Issued: {formatTimestamp(d.issued_at || d.timestamp)}
                    </div>
                    {lifecycleStatus !== 'DISMISSED' && lifecycleStatus !== 'SUPERSEDED' && (
                      <button
                        onClick={(e) => { e.stopPropagation(); requestDismiss(d.directive_id, d.title || d.content?.slice(0, 60)) }}
                        disabled={dismissing === d.directive_id}
                        className="text-xs px-3 py-1.5 rounded-lg bg-brand-accent/10 border border-brand-accent/30 text-brand-accent hover:bg-brand-accent hover:text-white transition-colors disabled:opacity-50"
                      >
                        {dismissing === d.directive_id ? 'Dismissing...' : 'Dismiss'}
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}

        {!loading && directives.length === 0 && (
          <div className="text-brand-text-tertiary text-center py-8">No directives issued yet.</div>
        )}
      </div>

      <ConfirmModal
        open={confirmModal.open && confirmModal.directiveId != null}
        title="Dismiss Directive"
        message={`Dismiss "${confirmModal.title}"? This will remove it from the outbound project stream. The directive record will be kept for audit history.`}
        confirmLabel="Dismiss Directive"
        cancelLabel="Cancel"
        destructive
        onConfirm={executeDismiss}
        onCancel={() => setConfirmModal({ open: false, directiveId: null, title: '' })}
      />
    </div>
  )
}
