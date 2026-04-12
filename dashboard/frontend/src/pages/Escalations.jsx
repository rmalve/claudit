import { useState, useEffect, useRef } from 'react'
import { useApi } from '../hooks/useApi'
import { formatTimestamp } from '../utils/time'
import SeverityBadge from '../components/SeverityBadge'
import ConfirmModal from '../components/ConfirmModal'

const resolutionStatusColors = {
  OPEN: { bg: 'bg-brand-bg-tertiary', text: 'text-brand-text-tertiary', label: 'Open' },
  AWAITING_USER: { bg: 'bg-[#C4A95B]/15', text: 'text-[#C4A95B]', label: 'Awaiting You' },
  DISMISSED: { bg: 'bg-brand-blue/15', text: 'text-brand-blue', label: 'Dismissed' },
  RESOLVED: { bg: 'bg-brand-green/15', text: 'text-brand-green', label: 'Resolved' },
}

function ResolutionBadge({ status }) {
  const s = resolutionStatusColors[status] || resolutionStatusColors.OPEN
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${s.bg} ${s.text}`}>
      {s.label}
    </span>
  )
}

function ConversationThread({ escalationId, resolutionStatus }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [showDismissModal, setShowDismissModal] = useState(false)
  const messagesEndRef = useRef(null)

  const fetchMessages = async () => {
    try {
      const res = await fetch(`/api/escalations/${escalationId}/messages`)
      const data = await res.json()
      setMessages(data.messages || [])
    } catch {
      // silent
    }
  }

  useEffect(() => {
    fetchMessages()
    const interval = setInterval(fetchMessages, 5000)
    return () => clearInterval(interval)
  }, [escalationId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    if (!input.trim() || sending) return
    setSending(true)
    try {
      await fetch(`/api/escalations/${escalationId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: input.trim() }),
      })
      setInput('')
      await fetchMessages()
    } finally {
      setSending(false)
    }
  }

  const requestDismiss = () => {
    if (!input.trim()) return
    setShowDismissModal(true)
  }

  const executeDismiss = async () => {
    setShowDismissModal(false)
    setSending(true)
    try {
      await fetch(`/api/escalations/${escalationId}/dismiss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ guidance: input.trim() }),
      })
      setInput('')
      await fetchMessages()
      window.dispatchEvent(new Event('escalation-updated'))
    } finally {
      setSending(false)
    }
  }

  const isClosed = resolutionStatus === 'DISMISSED' || resolutionStatus === 'RESOLVED'

  return (
    <div className="mt-3">
      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-2">Conversation</div>

      <div className="bg-brand-bg-secondary rounded-lg border border-brand-border max-h-80 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 && (
          <div className="text-xs text-brand-text-tertiary text-center py-4">No messages yet.</div>
        )}
        {messages.map((msg, i) => (
          <div
            key={msg.message_id || i}
            className={`flex ${msg.author === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div className={`max-w-[80%] rounded-lg px-3 py-2 ${
              msg.author === 'user'
                ? 'bg-brand-accent/10 border border-brand-accent/20'
                : 'bg-brand-surface border border-brand-border'
            }`}>
              <div className="flex items-center gap-2 mb-1">
                <span className={`text-[10px] font-semibold uppercase ${
                  msg.author === 'user' ? 'text-brand-accent' : 'text-brand-text-tertiary'
                }`}>
                  {msg.author === 'user' ? 'You' : 'Director'}
                </span>
                <span className="text-[10px] text-brand-text-tertiary">
                  {formatTimestamp(msg.timestamp)}
                </span>
              </div>
              <p className="text-xs text-brand-text-secondary whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {!isClosed && (
        <div className="mt-2 flex gap-2">
          <textarea
            className="flex-1 bg-brand-surface border border-brand-border rounded-md px-3 py-2 text-sm text-brand-text
                       placeholder-brand-text-tertiary resize-none focus:outline-none focus:border-brand-accent transition-colors"
            rows={2}
            placeholder="Type guidance here..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleSend()
              }
            }}
          />
          <div className="flex flex-col gap-1">
            <button
              className="px-3 py-1.5 bg-brand-bg-secondary hover:bg-brand-bg-tertiary text-brand-text text-xs rounded-md transition-colors disabled:opacity-50"
              onClick={handleSend}
              disabled={!input.trim() || sending}
            >
              Send
            </button>
            <button
              className="px-3 py-1.5 bg-brand-accent/80 hover:bg-brand-accent text-white text-xs rounded-md transition-colors disabled:opacity-50"
              onClick={requestDismiss}
              disabled={!input.trim() || sending}
              title="Dismiss with final guidance — closes the escalation"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <ConfirmModal
        open={showDismissModal}
        title="Dismiss Escalation"
        message="Dismiss this escalation with your current message as final guidance? The Director will act on your guidance."
        confirmLabel="Dismiss Escalation"
        cancelLabel="Cancel"
        destructive
        onConfirm={executeDismiss}
        onCancel={() => setShowDismissModal(false)}
      />
    </div>
  )
}

export default function Escalations() {
  const [selected, setSelected] = useState(null)
  const { data, loading, refetch } = useApi('/api/escalations', { refreshInterval: 15000 })

  useEffect(() => {
    const handler = () => refetch?.()
    window.addEventListener('escalation-updated', handler)
    return () => window.removeEventListener('escalation-updated', handler)
  }, [refetch])

  const escalations = data?.escalations || []

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-brand-text">Escalations</h1>
        <span className="text-sm text-brand-text-tertiary">{escalations.length} total</span>
      </div>

      {loading && <div className="text-brand-text-tertiary">Loading...</div>}

      <div className="space-y-2">
        {escalations.map((e, i) => {
          const resStatus = e.resolution_status || 'OPEN'
          const isPromotionFailure = (e.escalation_type || e.category || '').toUpperCase() === 'PROMOTION_FAILURE'
          const hasConversation = isPromotionFailure || resStatus === 'AWAITING_USER'

          return (
            <div
              key={e.escalation_id || i}
              className={`bg-brand-surface border rounded-lg p-4 cursor-pointer transition-colors ${
                selected === i ? 'border-brand-accent' : 'border-brand-border hover:border-brand-accent/50'
              } ${resStatus === 'AWAITING_USER' ? 'ring-1 ring-[#C4A95B]/30' : ''}`}
              onClick={() => setSelected(selected === i ? null : i)}
            >
              <div className="flex items-start gap-3">
                <SeverityBadge severity={e.severity} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-brand-text">
                      {e.title || e.summary?.slice(0, 100) || '(no title)'}
                    </span>
                    <ResolutionBadge status={resStatus} />
                    {isPromotionFailure && (
                      <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-[#8B6AAE]/15 text-[#8B6AAE]">
                        PROMOTION
                      </span>
                    )}
                  </div>
                  <div className="flex gap-3 mt-1 text-xs text-brand-text-tertiary">
                    <span>Type: {e.category || e.escalation_type || '—'}</span>
                    <span>Agent: {e.subject_agent || '—'}</span>
                    {e.promotion_id && <span>Promotion: {e.promotion_id}</span>}
                  </div>
                </div>
              </div>

              {selected === i && (
                <div className="mt-4 space-y-3 border-t border-brand-border pt-3" onClick={(ev) => ev.stopPropagation()}>
                  {e.summary && (
                    <div>
                      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Summary</div>
                      <p className="text-sm text-brand-text-secondary">{e.summary}</p>
                    </div>
                  )}
                  {e.impact_assessment && (
                    <div>
                      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Impact Assessment</div>
                      <p className="text-sm text-brand-text-secondary">{e.impact_assessment}</p>
                    </div>
                  )}
                  {e.recommended_action && (
                    <div>
                      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Recommended Action</div>
                      <p className="text-sm text-brand-text-secondary">{e.recommended_action}</p>
                    </div>
                  )}
                  {(e.pros_of_action?.length > 0 || e.cons_of_action?.length > 0) && (
                    <div className="grid grid-cols-2 gap-4">
                      {e.pros_of_action?.length > 0 && (
                        <div>
                          <div className="text-[11px] font-semibold text-brand-green uppercase tracking-wider mb-1">Pros</div>
                          <ul className="text-xs text-brand-text-secondary space-y-1">
                            {e.pros_of_action.map((p, j) => <li key={j}>+ {p}</li>)}
                          </ul>
                        </div>
                      )}
                      {e.cons_of_action?.length > 0 && (
                        <div>
                          <div className="text-[11px] font-semibold text-brand-red uppercase tracking-wider mb-1">Cons</div>
                          <ul className="text-xs text-brand-text-secondary space-y-1">
                            {e.cons_of_action.map((c, j) => <li key={j}>- {c}</li>)}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                  {e.metrics && (
                    <div>
                      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Metrics</div>
                      <pre className="text-xs text-brand-text-secondary bg-brand-bg-secondary rounded-md p-3 overflow-x-auto">
                        {JSON.stringify(e.metrics, null, 2)}
                      </pre>
                    </div>
                  )}
                  {(e.finding_refs?.length > 0 || e.finding_ids?.length > 0) && (
                    <div>
                      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1">Related Findings</div>
                      <div className="flex flex-wrap gap-1">
                        {(e.finding_refs || e.finding_ids || []).map((ref, j) => (
                          <code key={j} className="text-xs font-mono bg-brand-bg-secondary px-2 py-0.5 rounded text-brand-text-tertiary">{ref}</code>
                        ))}
                      </div>
                    </div>
                  )}

                  {hasConversation && (
                    <ConversationThread
                      escalationId={e.escalation_id}
                      resolutionStatus={resStatus}
                    />
                  )}

                  <div className="text-xs text-brand-text-tertiary">
                    ID: {e.escalation_id || '—'} | Directive: {e.directive_id || '—'} | {formatTimestamp(e.timestamp)}
                    {e.resolution_timestamp && ` | Resolved: ${formatTimestamp(e.resolution_timestamp)}`}
                  </div>
                </div>
              )}
            </div>
          )
        })}

        {!loading && escalations.length === 0 && (
          <div className="text-brand-text-tertiary text-center py-8">No escalations. All clear.</div>
        )}
      </div>
    </div>
  )
}
