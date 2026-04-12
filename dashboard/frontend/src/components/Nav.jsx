import { NavLink } from 'react-router-dom'
import { useThemeMode } from './MuiTheme'
import { useApi } from '../hooks/useApi'
import logoLight from '../assets/claudit-logo.svg'
import logoDark from '../assets/claudit-logo-dark.svg'

const links = [
  { to: '/', label: 'Overview' },
  { to: '/findings', label: 'Findings' },
  { to: '/directives', label: 'Directives' },
  { to: '/escalations', label: 'Escalations' },
  { to: '/sessions', label: 'Sessions' },
  { to: '/evals', label: 'Evals' },
  { to: '/reports', label: 'Reports' },
  { to: '/data-quality', label: 'Data Quality' },
  { to: '/system', label: 'System' },
]

export default function Nav() {
  const { mode, toggleMode } = useThemeMode()
  const { data: health } = useApi('/api/health', { refreshInterval: 10000 })

  return (
    <nav className="bg-brand-surface border-b border-brand-border px-6 flex items-center gap-6"
         style={{ height: 'var(--nav-height)' }}>
      <NavLink to="/" className="shrink-0">
        <img
          src={mode === 'dark' ? logoDark : logoLight}
          alt="Claudit"
          className="h-20"
        />
      </NavLink>
      <div className="flex gap-0.5 overflow-x-auto">
        {links.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `px-3 py-1.5 rounded-md text-sm font-medium transition-colors whitespace-nowrap ${
                isActive
                  ? 'bg-brand-accent/10 text-brand-accent-dark'
                  : 'text-brand-text-tertiary hover:text-brand-text hover:bg-brand-bg-secondary'
              }`
            }
          >
            {label}
          </NavLink>
        ))}
      </div>
      <div className="ml-auto shrink-0 flex items-center gap-3">
        <div className="flex items-center gap-2 text-[11px]">
          <span className={`flex items-center gap-1 ${health?.qdrant ? 'text-brand-green' : 'text-brand-red'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${health?.qdrant ? 'bg-brand-green' : 'bg-brand-red'}`} />
            QDrant
          </span>
          <span className={`flex items-center gap-1 ${health?.redis ? 'text-brand-green' : 'text-brand-red'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${health?.redis ? 'bg-brand-green' : 'bg-brand-red'}`} />
            Redis
          </span>
          <span className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full ${
            health?.pending_audit > 0
              ? 'bg-brand-accent/10 text-brand-accent'
              : 'bg-brand-green/10 text-brand-green'
          }`} title="Events awaiting audit">
            {health?.pending_audit != null ? health.pending_audit.toLocaleString() : '—'} pending
          </span>
        </div>
        <button
          onClick={toggleMode}
          className="px-2.5 py-1.5 rounded-md text-xs font-medium text-brand-text-tertiary
                     hover:text-brand-text hover:bg-brand-bg-secondary transition-colors"
          title={`Switch to ${mode === 'light' ? 'dark' : 'light'} mode`}
        >
          {mode === 'light' ? '●  Dark' : '○  Light'}
        </button>
      </div>
    </nav>
  )
}
