import { useEffect, useState } from 'react'
import { formatTimestamp } from '../utils/time'

const HAPPY_PATH = ['PENDING', 'ACKNOWLEDGED', 'VERIFICATION_PENDING', 'VERIFIED_COMPLIANT']

const STATE_LABELS = {
  PENDING: 'Pending',
  ACKNOWLEDGED: 'Ack',
  VERIFICATION_PENDING: 'Verify',
  VERIFIED_COMPLIANT: 'Verified',
  VERIFIED_NON_COMPLIANT: 'Not Verified',
  NON_COMPLIANT: 'Non-Compliant',
  ESCALATED: 'Escalated',
  DISMISSED: 'Dismissed',
  SUPERSEDED: 'Superseded',
}

// Compute the terminal state reached by any directive given its transitions.
// Returns { terminalState, lastTransition, happyPathIndex, failed, dismissed, superseded }
function analyzeTransitions(transitions) {
  if (!transitions || transitions.length === 0) {
    return { terminalState: 'PENDING', happyPathIndex: -1, failed: false, dismissed: false, superseded: false }
  }
  const byCycleThenStatus = [...transitions]
  const last = byCycleThenStatus[byCycleThenStatus.length - 1]
  const terminalState = last.to_status
  const dismissed = transitions.some(t => t.to_status === 'DISMISSED')
  const superseded = transitions.some(t => t.to_status === 'SUPERSEDED')
  const failed = ['VERIFIED_NON_COMPLIANT', 'NON_COMPLIANT', 'ESCALATED'].includes(terminalState)

  // Track furthest progress along the happy path
  let happyPathIndex = -1
  for (const t of transitions) {
    const idx = HAPPY_PATH.indexOf(t.to_status)
    if (idx > happyPathIndex) happyPathIndex = idx
  }
  return { terminalState, lastTransition: last, happyPathIndex, failed, dismissed, superseded }
}

// Return map from happy-path state name → transition row (if reached)
function buildStepMap(transitions) {
  const map = {}
  for (const t of transitions || []) {
    if (HAPPY_PATH.includes(t.to_status) && !(t.to_status in map)) {
      map[t.to_status] = t
    }
  }
  return map
}

export default function DirectiveLifecycleTimeline({ directiveId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!directiveId) return
    setLoading(true)
    setError(null)
    fetch(`/api/directives/${directiveId}/lifecycle`)
      .then(r => {
        if (!r.ok) throw new Error(`${r.status}`)
        return r.json()
      })
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [directiveId])

  if (!directiveId) return null
  if (loading) return <div className="text-xs text-brand-text-tertiary py-2">Loading lifecycle…</div>
  if (error) return <div className="text-xs text-brand-red py-2">Lifecycle error: {error}</div>
  if (!data) return null

  const transitions = data.transitions || []
  const { terminalState, happyPathIndex, failed, dismissed, superseded } = analyzeTransitions(transitions)
  const stepMap = buildStepMap(transitions)

  // Progress calculation
  // Each happy-path step is 25% of the bar width.
  const greenFillPct = happyPathIndex >= 0 ? ((happyPathIndex + 1) / HAPPY_PATH.length) * 100 : 0
  let tailColor = null
  let tailPct = 0
  if (failed) {
    tailColor = 'bg-brand-red'
    tailPct = 100 - greenFillPct
  } else if (dismissed) {
    tailColor = 'bg-brand-bg-tertiary'
    tailPct = 100 - greenFillPct
  } else if (superseded) {
    tailColor = 'bg-brand-text-tertiary/30'
    tailPct = 100 - greenFillPct
  }

  return (
    <div>
      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-3">
        Lifecycle Timeline
      </div>

      <div className="relative px-4 pt-4 pb-6 bg-brand-bg-secondary rounded-md">
        {/* Background track */}
        <div className="absolute left-[calc(1rem+11px)] right-[calc(1rem+11px)] top-[calc(1rem+10px)] h-[6px] bg-brand-bg-tertiary rounded-full overflow-hidden">
          {greenFillPct > 0 && (
            <div
              className="absolute left-0 top-0 h-full bg-brand-green shadow-[0_0_8px_rgba(74,222,128,0.5)]"
              style={{ width: `${greenFillPct}%` }}
            />
          )}
          {tailColor && tailPct > 0 && (
            <div
              className={`absolute top-0 h-full ${tailColor}`}
              style={{ left: `${greenFillPct}%`, width: `${tailPct}%` }}
            />
          )}
        </div>

        {/* Step markers along the track */}
        <div className="relative flex items-start justify-between">
          {HAPPY_PATH.map((state, i) => {
            const reached = stepMap[state]
            const isCurrent = !failed && !dismissed && !superseded && terminalState === state
            const dotColor = reached
              ? 'bg-brand-green border-brand-bg'
              : (isCurrent ? 'bg-brand-accent border-brand-bg' : 'bg-brand-bg-tertiary border-brand-bg')
            const ring = isCurrent ? 'ring-2 ring-brand-accent/40' : ''
            return (
              <div key={state} className="flex flex-col items-center" style={{ width: '22px' }}>
                <div className={`w-[22px] h-[22px] rounded-full border-2 ${dotColor} ${ring}`} />
                <div className={`text-[10px] mt-1.5 whitespace-nowrap ${reached ? 'text-brand-green' : (isCurrent ? 'text-brand-accent' : 'text-brand-text-tertiary')}`}>
                  {STATE_LABELS[state]}
                </div>
                <div className="text-[9px] text-brand-text-tertiary mt-0.5">
                  {reached?.audit_cycle_id ? reached.audit_cycle_id.split('-').pop().slice(0, 6) : '—'}
                </div>
              </div>
            )
          })}
        </div>

        {/* Terminal non-happy-path indicator */}
        {(failed || dismissed || superseded) && (
          <div className={`mt-4 text-xs rounded-md p-2 border ${
            failed ? 'bg-brand-red/10 border-brand-red/30 text-brand-red' :
            dismissed ? 'bg-brand-bg-tertiary border-brand-border text-brand-text-tertiary' :
            'bg-brand-bg-tertiary border-brand-border text-brand-text-tertiary'
          }`}>
            Terminal state: <span className="font-semibold">{STATE_LABELS[terminalState]}</span>
          </div>
        )}
      </div>

      {/* Full transition list — click-to-expand detail */}
      <div className="mt-3 space-y-1">
        {transitions.map((t, i) => (
          <div key={i} className="flex items-center gap-2 text-[11px] text-brand-text-tertiary">
            <span className="text-brand-text-secondary min-w-[90px]">{STATE_LABELS[t.to_status] || t.to_status}</span>
            <span className="font-mono text-[10px]">{t.audit_cycle_id || '—'}</span>
            <span>{formatTimestamp(t.transition_timestamp)}</span>
            <span className="text-brand-text-tertiary/70 italic">{t.trigger}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
