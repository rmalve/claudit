import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import { formatTimestamp } from '../utils/time'

const eventStyles = {
  user_text: { badge: 'User', style: 'bg-brand-accent/10 text-brand-accent' },
  thinking: { badge: 'Thinking', style: 'bg-[#8B6AAE]/10 text-[#8B6AAE]' },
  assistant_text: { badge: 'Assistant', style: 'bg-brand-blue/10 text-brand-blue' },
  tool_use: { badge: '', style: 'bg-brand-green/10 text-brand-green' },
  tool_result: { badge: 'Result', style: 'bg-brand-bg-tertiary text-brand-text-tertiary' },
}

const confidenceBorder = {
  high: 'border-brand-border-strong',
  medium: 'border-dashed border-brand-border',
  low: 'border-dotted border-brand-text-tertiary/30',
}

function TurnEventRow({ event }) {
  const [expanded, setExpanded] = useState(false)
  const config = eventStyles[event.type] || eventStyles.tool_result

  let badge = config.badge
  let summary = ''
  let hasDetail = false

  if (event.type === 'user_text') {
    summary = event.text?.slice(0, 120) || ''
    hasDetail = event.text?.length > 120
  } else if (event.type === 'thinking') {
    summary = event.text?.slice(0, 80) || '(reasoning)'
    hasDetail = event.text?.length > 80
  } else if (event.type === 'assistant_text') {
    summary = event.text?.slice(0, 120) || ''
    hasDetail = event.text?.length > 120
  } else if (event.type === 'tool_use') {
    badge = event.tool_name || 'Tool'
    summary = event.input_summary || ''
    hasDetail = !!event.input_summary
    if (event.subagent_type) {
      summary = `→ ${event.subagent_type}: ${summary}`
    }
  } else if (event.type === 'tool_result') {
    summary = event.text?.slice(0, 120) || '(empty result)'
    hasDetail = event.text?.length > 120
  }

  return (
    <div className="group">
      <div
        className={`flex items-start gap-2 py-1 px-2 rounded cursor-pointer transition-colors hover:bg-brand-bg-secondary ${event.is_error ? 'bg-brand-red/5' : ''}`}
        onClick={() => hasDetail && setExpanded(!expanded)}
      >
        <span className={`text-[11px] font-mono px-1.5 py-0.5 rounded shrink-0 mt-0.5 ${event.is_error ? 'bg-brand-red/10 text-brand-red' : config.style}`}>
          {badge}
        </span>
        <span className="text-xs text-brand-text-secondary leading-relaxed">{summary}</span>
        {hasDetail && (
          <span className="text-[10px] text-brand-text-tertiary ml-auto opacity-0 group-hover:opacity-100 shrink-0">
            {expanded ? '▾' : '▸'}
          </span>
        )}
      </div>

      {expanded && event.text && (
        <div className="ml-16 mb-2 text-xs bg-brand-bg-secondary rounded-md p-3 overflow-x-auto">
          <pre className="text-brand-text-secondary whitespace-pre-wrap font-mono text-[11px] leading-relaxed">
            {event.text}
          </pre>
        </div>
      )}
    </div>
  )
}

function SubagentSection({ subagent }) {
  const [expanded, setExpanded] = useState(false)
  const turnCount = subagent.turns?.length || 0
  const totalTools = subagent.turns?.reduce((sum, t) => sum + (t.tool_call_count || 0), 0) || 0

  return (
    <div className="mt-2 mb-2">
      <div
        className="flex items-center gap-2 py-1.5 px-2 rounded-lg bg-brand-accent/5 border border-brand-accent/20 cursor-pointer hover:bg-brand-accent/10 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-brand-accent text-sm">{expanded ? '▾' : '▸'}</span>
        <span className="text-xs font-medium text-brand-accent">
          {subagent.agent_type || 'Subagent'}
        </span>
        <span className="text-[10px] text-brand-text-tertiary">
          {turnCount} turn{turnCount !== 1 ? 's' : ''}, {totalTools} tool calls
        </span>
      </div>

      {expanded && (
        <div className="ml-4 mt-1 border-l-2 border-brand-accent/20 pl-3">
          {(subagent.turns || []).map((turn, i) => (
            <PromptTurn key={i} turn={turn} depth={1} />
          ))}
        </div>
      )}
    </div>
  )
}

function PromptTurn({ turn, depth = 0 }) {
  const [collapsed, setCollapsed] = useState(depth > 0)
  const [promptExpanded, setPromptExpanded] = useState(false)
  const borderClass = confidenceBorder[turn.boundary_confidence] || confidenceBorder.high
  const events = turn.events || []
  const hasEvents = events.length > 0

  const promptText = turn.user_prompt || ''
  const isTruncated = promptText.length > 80
  const turnLabel = isTruncated && !promptExpanded
    ? promptText.slice(0, 80) + '...'
    : promptText || `Turn ${turn.turn_index + 1}`

  const toolCount = turn.tool_call_count || events.filter(e => e.type === 'tool_use').length
  const agentCount = turn.subagent_spawns?.length || 0

  return (
    <div className={`${turn.turn_index > 0 ? 'mt-3' : ''}`}>
      <div
        className="flex items-start gap-2 py-1.5 cursor-pointer hover:bg-brand-bg-secondary rounded px-1 transition-colors"
        onClick={() => setCollapsed(!collapsed)}
      >
        <span className="text-brand-accent text-sm mt-0.5">{collapsed ? '▸' : '▾'}</span>
        <span className={`text-sm font-medium text-brand-accent ${promptExpanded ? 'whitespace-pre-wrap' : 'truncate max-w-lg'}`}>
          {turnLabel}
        </span>
        {isTruncated && (
          <button
            onClick={(e) => { e.stopPropagation(); setPromptExpanded(!promptExpanded) }}
            className="text-[10px] px-1.5 py-0.5 rounded bg-brand-accent/10 text-brand-accent hover:bg-brand-accent/20 transition-colors shrink-0 mt-0.5"
          >
            {promptExpanded ? 'less' : 'more'}
          </button>
        )}
        {toolCount > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-brand-green/10 text-brand-green shrink-0">
            {toolCount} tool{toolCount !== 1 ? 's' : ''}
          </span>
        )}
        {turn.thinking_count > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#8B6AAE]/10 text-[#8B6AAE] shrink-0">
            {turn.thinking_count} thinking
          </span>
        )}
        {agentCount > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-brand-accent/10 text-brand-accent shrink-0">
            {agentCount} agent{agentCount !== 1 ? 's' : ''}
          </span>
        )}
        {turn.boundary_confidence && turn.boundary_confidence !== 'high' && turn.turn_index > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-brand-bg-tertiary text-brand-text-tertiary shrink-0">
            inferred
          </span>
        )}
        <span className="text-[10px] text-brand-text-tertiary shrink-0 ml-auto">
          {formatTimestamp(turn.start_time)}
        </span>
      </div>

      {!collapsed && (
        <div className={`ml-4 border-l-2 ${borderClass} pl-3`}>
          {hasEvents ? (
            events.filter(e => e.type !== 'user_text').map((event, j) => (
              <div key={j}>
                <TurnEventRow event={event} />
                {/* Inline subagent after Agent tool_use event */}
                {event.type === 'tool_use' && event.subagent_type && turn.subagents?.length > 0 && (
                  turn.subagents
                    .filter(sub => sub.agent_type?.toLowerCase().includes(event.subagent_type?.toLowerCase())
                      || event.subagent_type?.toLowerCase().includes(sub.agent_type?.toLowerCase())
                      || ['general', 'general-purpose', 'Explore'].includes(event.subagent_type))
                    .slice(0, 1)
                    .map((sub, si) => <SubagentSection key={si} subagent={sub} />)
                )}
              </div>
            ))
          ) : (
            <>
              {turn.user_prompt && (
                <div className="bg-brand-bg-secondary rounded-lg p-3 my-1.5">
                  <div className="text-[10px] font-semibold text-brand-accent uppercase tracking-wider mb-1">User</div>
                  <p className="text-sm text-brand-text whitespace-pre-wrap">{turn.user_prompt}</p>
                </div>
              )}
              {turn.tool_call_names?.length > 0 && (
                <div className="text-xs text-brand-text-tertiary py-1 px-2">
                  Tools: {turn.tool_call_names.join(', ')}
                </div>
              )}
              {turn.assistant_response && (
                <div className="bg-brand-surface border border-brand-border rounded-lg p-3 my-1.5">
                  <div className="text-[10px] font-semibold text-brand-blue uppercase tracking-wider mb-1">Assistant</div>
                  <p className="text-sm text-brand-text-secondary whitespace-pre-wrap">
                    {turn.assistant_response.length > 1000
                      ? turn.assistant_response.slice(0, 1000) + '...'
                      : turn.assistant_response}
                  </p>
                </div>
              )}
            </>
          )}

          {/* Any subagents not matched to a specific tool_use event */}
          {turn.subagents?.length > 0 && !hasEvents && (
            turn.subagents.map((sub, i) => (
              <SubagentSection key={i} subagent={sub} />
            ))
          )}
        </div>
      )}
    </div>
  )
}

export default function SessionTree({ data, depth = 0 }) {
  if (!data || !data.prompt_turns) return null

  return (
    <div className={depth > 0 ? '' : 'space-y-1'}>
      {data.prompt_turns.map((turn) => (
        <PromptTurn key={turn.turn_index} turn={turn} depth={depth} />
      ))}
    </div>
  )
}
