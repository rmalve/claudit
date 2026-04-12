import { useApi } from '../hooks/useApi'
import Card from '../components/Card'

export default function DataQuality() {
  const { data, loading } = useApi('/api/data-quality?limit=50', { refreshInterval: 30000 })

  const events = data?.events || []

  const fieldCounts = {}
  const ownerCounts = {}
  events.forEach((e) => {
    const p = e.payload || {}
    const fields = p.missing_fields || []
    const owners = p.missing_field_owners || {}
    fields.forEach((f) => {
      fieldCounts[f] = (fieldCounts[f] || 0) + 1
      const owner = owners[f] || 'unknown'
      ownerCounts[owner] = (ownerCounts[owner] || 0) + 1
    })
  })

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-brand-text">Data Quality</h1>
        <span className="text-sm text-brand-text-tertiary">{events.length} events sampled</span>
      </div>

      {loading && <div className="text-brand-text-tertiary">Loading...</div>}

      <div>
        <h2 className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-2">Missing Field Frequency</h2>
        <div className="grid grid-cols-3 gap-3">
          {Object.entries(fieldCounts)
            .sort((a, b) => b[1] - a[1])
            .map(([field, count]) => (
              <Card key={field} title={field} value={count} subtitle="events missing this field" />
            ))}
        </div>
      </div>

      <div>
        <h2 className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-2">Issues by Owner</h2>
        <div className="grid grid-cols-3 gap-3">
          {Object.entries(ownerCounts)
            .sort((a, b) => b[1] - a[1])
            .map(([owner, count]) => (
              <Card
                key={owner}
                title={owner}
                value={count}
                subtitle="missing field instances"
                className={
                  owner === 'hook' ? 'border-[#C4A95B]/30' :
                  owner === 'agent' ? 'border-brand-accent/30' :
                  owner === 'environment' ? 'border-brand-blue/30' :
                  ''
                }
              />
            ))}
        </div>
      </div>

      <div>
        <h2 className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-2">Recent Data Quality Events</h2>
        <div className="space-y-2">
          {events.slice(0, 20).map((e, i) => {
            const p = e.payload || {}
            return (
              <div key={i} className="bg-brand-surface border border-brand-border rounded-lg p-3">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-mono bg-brand-bg-secondary px-2 py-0.5 rounded-md">
                    {p.source_event_type || '?'}
                  </span>
                  <span className="text-xs text-brand-red">{p.error_count || 0} errors</span>
                  <span className="text-xs text-[#C4A95B]">{p.warning_count || 0} warnings</span>
                  <span className="text-xs text-brand-text-tertiary">{p.agent || '—'}</span>
                  <span className="text-xs text-brand-text-tertiary">{(p.session_id || '').slice(0, 8)}</span>
                </div>
                <div className="mt-1 text-xs text-brand-text-secondary">
                  Missing: {(p.missing_fields || []).join(', ')}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
