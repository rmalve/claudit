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

function analyzeTransitions(transitions) {
  if (!transitions || transitions.length === 0) {
    return { terminalState: 'PENDING', failed: false, dismissed: false, superseded: false }
  }
  const last = transitions[transitions.length - 1]
  const terminalState = last.to_status
  const dismissed = transitions.some(t => t.to_status === 'DISMISSED')
  const superseded = transitions.some(t => t.to_status === 'SUPERSEDED')
  const failed = ['VERIFIED_NON_COMPLIANT', 'NON_COMPLIANT', 'ESCALATED'].includes(terminalState)
  return { terminalState, failed, dismissed, superseded }
}

function buildStepMap(transitions) {
  const map = {}
  for (const t of transitions || []) {
    if (HAPPY_PATH.includes(t.to_status) && !(t.to_status in map)) {
      map[t.to_status] = t
    }
  }
  return map
}

export default function DirectiveLifecycleTimeline({ transitions: transitionsProp, lifecycleStatus }) {
  let transitions = transitionsProp || []

  // When SQLite transitions are empty but the parent computed a status from
  // live Redis data, synthesize transitions so the timeline matches.
  if (transitions.length === 0 && lifecycleStatus && lifecycleStatus !== 'PENDING') {
    const synth = [{ from_status: null, to_status: 'PENDING', trigger: 'directive_published' }]
    const idx = HAPPY_PATH.indexOf(lifecycleStatus)
    if (idx >= 1) {
      for (let i = 1; i <= idx; i++) {
        synth.push({ from_status: HAPPY_PATH[i - 1], to_status: HAPPY_PATH[i], trigger: 'live' })
      }
    } else if (!HAPPY_PATH.includes(lifecycleStatus)) {
      synth.push({ from_status: 'PENDING', to_status: lifecycleStatus, trigger: 'live' })
    }
    transitions = synth
  }

  const { terminalState, failed, dismissed, superseded } = analyzeTransitions(transitions)
  const stepMap = buildStepMap(transitions)

  return (
    <div className="flex items-center gap-2 mt-2">
      {HAPPY_PATH.map((state, i) => {
        const reached = stepMap[state]
        const isCurrent = !failed && !dismissed && !superseded && terminalState === state
        const dotColor = reached
          ? 'bg-brand-green'
          : (isCurrent ? 'bg-brand-accent ring-2 ring-brand-accent/30' : 'bg-brand-bg-tertiary')
        return (
          <div key={state} className="flex items-center">
            <div className={`w-2.5 h-2.5 rounded-full ${dotColor}`} />
            <span className={`text-[10px] mx-1 ${
              reached ? 'text-brand-green' : (isCurrent ? 'text-brand-accent' : 'text-brand-text-tertiary')
            }`}>
              {STATE_LABELS[state]}
            </span>
            {i < HAPPY_PATH.length - 1 && (
              <div className={`w-4 h-0.5 ${reached ? 'bg-brand-green' : 'bg-brand-border'}`} />
            )}
          </div>
        )
      })}
      {(failed || dismissed || superseded) && (
        <div className="flex items-center ml-1">
          <div className={`w-2.5 h-2.5 rounded-full ${failed ? 'bg-brand-red' : 'bg-brand-bg-tertiary'}`} />
          <span className={`text-[10px] mx-1 ${failed ? 'text-brand-red' : 'text-brand-text-tertiary'}`}>
            {STATE_LABELS[terminalState]}
          </span>
        </div>
      )}
    </div>
  )
}
