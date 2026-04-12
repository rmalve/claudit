export default function Card({ title, value, subtitle, className = '' }) {
  return (
    <div className={`bg-brand-surface border border-brand-border rounded-lg p-4 transition-shadow hover:shadow-sm ${className}`}>
      {title && (
        <div className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider mb-1.5">
          {title}
        </div>
      )}
      <div className="text-2xl font-bold text-brand-text">{value}</div>
      {subtitle && (
        <div className="text-xs text-brand-text-tertiary mt-1">{subtitle}</div>
      )}
    </div>
  )
}
