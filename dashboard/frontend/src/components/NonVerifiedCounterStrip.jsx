import { useNavigate } from 'react-router-dom'
import { useApi } from '../hooks/useApi'

const CATEGORIES = [
  { key: 'non_compliant', label: 'Non-Compliant', color: 'text-brand-red', bg: 'bg-brand-red/15', status: 'NON_COMPLIANT' },
  { key: 'verified_non_compliant', label: 'Verified Non-Compliant', color: 'text-brand-red', bg: 'bg-brand-red/20', status: 'VERIFIED_NON_COMPLIANT' },
  { key: 'escalated', label: 'Escalated', color: 'text-brand-accent', bg: 'bg-brand-accent/15', status: 'ESCALATED' },
  { key: 'dismissed', label: 'Dismissed', color: 'text-brand-text-tertiary', bg: 'bg-brand-bg-tertiary', status: 'DISMISSED' },
  { key: 'superseded', label: 'Superseded', color: 'text-brand-text-tertiary', bg: 'bg-brand-bg-tertiary', status: 'SUPERSEDED' },
]

export default function NonVerifiedCounterStrip({ project }) {
  const navigate = useNavigate()
  const { data, loading } = useApi(
    project ? `/api/metrics/non-verified-counts?project=${project}` : null,
    { refreshInterval: 60000 },
  )

  if (!project) return null
  const counts = data?.counts || {}

  return (
    <div className="bg-brand-surface border border-brand-border rounded-lg p-3">
      <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-2">
        Non-Verified Directives · {project}
      </div>
      <div className="flex flex-wrap gap-2">
        {CATEGORIES.map(cat => {
          const n = counts[cat.key] ?? 0
          return (
            <button
              key={cat.key}
              onClick={() => navigate(`/directives?project=${project}&status=${cat.status}`)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md ${cat.bg} ${cat.color} text-xs font-semibold hover:ring-1 hover:ring-current transition`}
              disabled={loading || n === 0}
            >
              <span>{cat.label}</span>
              <span className="font-mono">{loading ? '…' : n}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
