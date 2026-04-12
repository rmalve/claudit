export default function FilterBar({ filters, values, onChange }) {
  return (
    <div className="flex gap-4 flex-wrap">
      {filters.map(({ key, label, options }) => (
        <div key={key} className="flex items-center gap-2">
          <label className="text-[11px] font-semibold text-brand-text-tertiary uppercase tracking-wider">
            {label}
          </label>
          <select
            value={values[key] || ''}
            onChange={(e) => onChange({ ...values, [key]: e.target.value })}
            className="bg-brand-surface border border-brand-border rounded-md px-2.5 py-1.5 text-sm text-brand-text
                       focus:outline-none focus:border-brand-accent transition-colors"
          >
            <option value="">All</option>
            {options.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </div>
      ))}
    </div>
  )
}
