import { useMemo } from 'react'
import { useApi } from '../hooks/useApi'
import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'

// Parse cycle-id to extract the datetime portion for bucket ordering.
// Format: "cycle-YYYYMMDD-HHMMSS-xxxxxxxx"
function cycleKey(cycleId) {
  if (!cycleId) return ''
  const parts = cycleId.split('-')
  return parts.length >= 3 ? `${parts[1]}-${parts[2]}` : cycleId
}

// Extract a numeric "cycle index" from a cycle_id for Y-axis math.
// We use string ordering of the datetime portion, then rank by occurrence.
function bucketize(rows) {
  // Group directives by their verified_cycle (the cycle they finished in).
  const buckets = new Map()
  for (const r of rows) {
    if (!r.published_cycle || !r.verified_cycle) continue
    const key = r.verified_cycle
    if (!buckets.has(key)) buckets.set(key, [])
    buckets.get(key).push(r)
  }
  // For each bucket compute cycles-elapsed per directive.
  // Since cycle-ids are strings, we sort verified cycles and assign ordinals.
  const sortedVerifiedCycles = [...buckets.keys()].sort((a, b) => cycleKey(a).localeCompare(cycleKey(b)))
  const cycleOrdinal = new Map()
  sortedVerifiedCycles.forEach((c, i) => cycleOrdinal.set(c, i))

  // For elapsed: we need ordinal of published_cycle too. Collect all cycles.
  const allCycles = new Set()
  for (const r of rows) {
    if (r.published_cycle) allCycles.add(r.published_cycle)
    if (r.verified_cycle) allCycles.add(r.verified_cycle)
  }
  const sortedAll = [...allCycles].sort((a, b) => cycleKey(a).localeCompare(cycleKey(b)))
  const allOrdinal = new Map()
  sortedAll.forEach((c, i) => allOrdinal.set(c, i))

  // Build chart data: one entry per verified_cycle
  return sortedVerifiedCycles.map(verifiedCycle => {
    const directives = buckets.get(verifiedCycle)
    const elapsed = directives.map(d => {
      const p = allOrdinal.get(d.published_cycle) ?? 0
      const v = allOrdinal.get(d.verified_cycle) ?? 0
      return { directive_id: d.directive_id, cycles: v - p }
    }).sort((a, b) => a.cycles - b.cycles)

    const values = elapsed.map(e => e.cycles)
    const median = values.length ? values[Math.floor(values.length / 2)] : 0
    const p25 = values.length ? values[Math.floor(values.length * 0.25)] : 0
    const p75 = values.length ? values[Math.floor(values.length * 0.75)] : 0

    return {
      cycle: verifiedCycle.split('-').slice(1, 3).join(' '),
      median,
      p25,
      p75,
      band: [p25, p75],
      count: directives.length,
      directives: elapsed,
    }
  })
}

function TooltipContent({ active, payload }) {
  if (!active || !payload || payload.length === 0) return null
  const d = payload[0]?.payload
  if (!d) return null
  return (
    <div className="bg-brand-bg border border-brand-border rounded-md p-3 shadow-lg max-w-xs">
      <div className="text-xs font-semibold text-brand-text mb-1">{d.cycle}</div>
      <div className="text-[11px] text-brand-text-tertiary mb-2">
        median <span className="text-brand-green font-semibold">{d.median}</span> cycles ·
        IQR {d.p25}–{d.p75} · n={d.count}
      </div>
      <div className="border-t border-brand-border pt-2 space-y-1 max-h-40 overflow-y-auto">
        {d.directives.map(item => (
          <div key={item.directive_id} className="flex items-center justify-between gap-4 text-[10px] font-mono">
            <span className="text-brand-text-secondary truncate">{item.directive_id}</span>
            <span className="text-brand-green font-semibold">{item.cycles}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function CyclesToVerificationChart({ project, lastNCycles = 20 }) {
  const { data, loading, error } = useApi(
    project ? `/api/metrics/cycles-to-verification?project=${project}&last_n_cycles=${lastNCycles}` : null,
    { refreshInterval: 60000 },
  )

  const chartData = useMemo(() => bucketize(data?.directives || []), [data])

  if (!project) return null

  return (
    <div className="bg-brand-surface border border-brand-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-sm font-semibold text-brand-text">Cycles to Verification</div>
          <div className="text-[11px] text-brand-text-tertiary">
            median + IQR band · project {project}
          </div>
        </div>
        <div className="text-[10px] text-brand-text-tertiary">{chartData.length} buckets</div>
      </div>

      {loading && <div className="text-xs text-brand-text-tertiary py-8 text-center">Loading…</div>}
      {error && <div className="text-xs text-brand-red py-8 text-center">Error: {error}</div>}

      {!loading && !error && chartData.length === 0 && (
        <div className="text-xs text-brand-text-tertiary py-8 text-center">
          No verified directives yet for {project}.
        </div>
      )}

      {!loading && chartData.length > 0 && (
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={chartData} margin={{ top: 10, right: 16, left: 0, bottom: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--brand-border, #334155)" />
            <XAxis dataKey="cycle" stroke="var(--brand-text-tertiary, #94a3b8)" fontSize={10} />
            <YAxis stroke="var(--brand-text-tertiary, #94a3b8)" fontSize={10} label={{ value: 'cycles', angle: -90, position: 'insideLeft', fill: '#94a3b8', fontSize: 10 }} />
            <Tooltip content={<TooltipContent />} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Area
              type="monotone"
              dataKey="band"
              stroke="none"
              fill="#4a9eff"
              fillOpacity={0.2}
              name="IQR (p25–p75)"
            />
            <Line
              type="monotone"
              dataKey="median"
              stroke="#4a9eff"
              strokeWidth={2}
              dot={{ r: 3, fill: '#4a9eff' }}
              name="median"
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
