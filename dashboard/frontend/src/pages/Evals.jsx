import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import { formatTimestamp } from '../utils/time'
import { getTooltipStyle, getAxisTickStyle, getGridColor } from '../utils/chartTheme'
import FilterBar from '../components/FilterBar'
import Card from '../components/Card'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts'

const evalNameOptions = ['test_pass_rate', 'lint_check']
const passedOptions = ['true', 'false']

export default function Evals() {
  const [filters, setFilters] = useState({})
  const [selected, setSelected] = useState(null)

  const params = new URLSearchParams()
  if (filters.eval_name) params.set('eval_name', filters.eval_name)
  if (filters.passed) params.set('passed', filters.passed)
  params.set('limit', '100')

  const { data, loading } = useApi(`/api/evals?${params}`, { refreshInterval: 15000 })
  const { data: summaryData } = useApi('/api/evals/summary', { refreshInterval: 30000 })

  const evals = data?.evals || []
  const summaries = summaryData?.summary || []

  const barData = summaries.map(s => ({
    name: s.eval_name,
    pass_rate: s.pass_rate != null ? Math.round(s.pass_rate * 100) : 0,
    total: s.total,
    passed: s.passed,
    failed: s.failed,
  }))

  const tickStyle = getAxisTickStyle()
  const gridColor = getGridColor()

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-brand-text">Evals</h1>
        <span className="text-sm text-brand-text-tertiary">{evals.length} results</span>
      </div>

      {summaries.length > 0 && (
        <div className="grid grid-cols-3 gap-3">
          {summaries.map(s => (
            <Card
              key={s.eval_name}
              title={s.eval_name}
              value={s.pass_rate != null ? `${Math.round(s.pass_rate * 100)}%` : '—'}
              subtitle={`${s.passed}/${s.total} passed | avg score: ${s.avg_score != null ? s.avg_score.toFixed(2) : '—'}`}
              className={
                s.pass_rate >= 0.9 ? 'border-brand-green/30' :
                s.pass_rate >= 0.7 ? 'border-[#C4A95B]/30' :
                'border-brand-red/30'
              }
            />
          ))}
        </div>
      )}

      {barData.length > 0 && (
        <div className="bg-brand-surface border border-brand-border rounded-lg p-4">
          <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-2">Pass Rate by Eval Type</div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={barData}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
              <XAxis dataKey="name" tick={tickStyle} />
              <YAxis tick={tickStyle} domain={[0, 100]} unit="%" />
              <Tooltip contentStyle={getTooltipStyle()} formatter={(v) => `${v}%`} />
              <Bar dataKey="pass_rate" name="Pass Rate" radius={[4, 4, 0, 0]}>
                {barData.map((entry, i) => (
                  <Cell
                    key={i}
                    fill={entry.pass_rate >= 90 ? '#4D8C00' : entry.pass_rate >= 70 ? '#C4A95B' : '#D44A4A'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <FilterBar
        filters={[
          { key: 'eval_name', label: 'Eval', options: evalNameOptions },
          { key: 'passed', label: 'Passed', options: passedOptions },
        ]}
        values={filters}
        onChange={setFilters}
      />

      {loading && <div className="text-brand-text-tertiary">Loading...</div>}

      <div className="space-y-2">
        {evals.map((e, i) => {
          const p = e.payload || {}
          const score = p.score
          const passed = p.passed

          return (
            <div
              key={i}
              className={`bg-brand-surface border rounded-lg p-3 cursor-pointer transition-colors ${
                selected === i ? 'border-brand-accent' : 'border-brand-border hover:border-brand-accent/50'
              }`}
              onClick={() => setSelected(selected === i ? null : i)}
            >
              <div className="flex items-center gap-3">
                <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${passed ? 'bg-brand-green' : 'bg-brand-red'}`} />
                <span className="text-xs font-mono bg-brand-bg-secondary px-2 py-0.5 rounded-md">
                  {p.eval_name || '?'}
                </span>
                <span className="text-sm text-brand-text flex-1">
                  {p.details || '—'}
                </span>
                {score != null && (
                  <span className={`text-xs font-bold ${
                    score >= 0.9 ? 'text-brand-green' : score >= 0.7 ? 'text-[#C4A95B]' : 'text-brand-red'
                  }`}>
                    {(score * 100).toFixed(0)}%
                  </span>
                )}
                <span className="text-xs text-brand-text-tertiary">
                  {(p.session_id || '').slice(0, 8)}
                </span>
              </div>

              {selected === i && (
                <div className="mt-3 border-t border-brand-border pt-2 space-y-2">
                  <div className="grid grid-cols-4 gap-2 text-xs">
                    <div>
                      <span className="text-brand-text-tertiary">Agent: </span>
                      <span className="text-brand-text-secondary">{p.agent || '—'}</span>
                    </div>
                    <div>
                      <span className="text-brand-text-tertiary">Version: </span>
                      <span className="text-brand-text-secondary">{p.agent_version || '—'}</span>
                    </div>
                    <div>
                      <span className="text-brand-text-tertiary">Project: </span>
                      <span className="text-brand-text-secondary">{p.project || '—'}</span>
                    </div>
                    <div>
                      <span className="text-brand-text-tertiary">Time: </span>
                      <span className="text-brand-text-secondary">
                        {formatTimestamp(p.timestamp)}
                      </span>
                    </div>
                  </div>
                  {e.text && (
                    <pre className="text-xs text-brand-text-secondary bg-brand-bg-secondary rounded-md p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                      {e.text}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )
        })}

        {!loading && evals.length === 0 && (
          <div className="text-brand-text-tertiary text-center py-8">
            No eval results yet. Evals are produced when agents edit .py files (test pass rate, lint checks).
          </div>
        )}
      </div>
    </div>
  )
}
