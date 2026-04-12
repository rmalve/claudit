const colors = {
  critical: 'bg-brand-red text-white',
  high: 'bg-brand-accent text-white',
  medium: 'bg-[#C4A95B] text-brand-text',
  low: 'bg-brand-blue text-white',
  info: 'bg-[#7A7A72] text-white',
}

export default function SeverityBadge({ severity }) {
  const s = (severity || '').toLowerCase()
  const cls = colors[s] || 'bg-brand-bg-tertiary text-brand-text-secondary'
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide ${cls}`}>
      {severity || 'unknown'}
    </span>
  )
}
