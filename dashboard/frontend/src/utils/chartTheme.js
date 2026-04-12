// Centralized chart colors and styling for Recharts
// Matches the Anthropic brand palette

export const CHART_COLORS = [
  '#D97757', // clay
  '#6B8F3C', // olive
  '#3B82D9', // sky
  '#8B6AAE', // heather
  '#A0527A', // fig
  '#4A8C6F', // cactus
  '#C4A95B', // sand
  '#7A7A72', // slate
]

export const SEVERITY_COLORS = {
  critical: '#D44A4A',
  high: '#D97757',
  medium: '#C4A95B',
  low: '#3B82D9',
  info: '#7A7A72',
}

export const AUDITOR_COLORS = {
  trace: '#3B82D9',
  safety: '#4A8C6F',
  policy: '#8B6AAE',
  hallucination: '#A0527A',
  drift: '#6B8F3C',
  cost: '#C4A95B',
  director: '#7A7A72',
}

export const FINDING_TYPE_COLORS = {
  violation: '#D44A4A',
  anomaly: '#D97757',
  trend: '#3B82D9',
  info: '#7A7A72',
}

export const CONFIDENCE_COLORS = {
  '0.9-1.0': '#D44A4A',
  '0.7-0.9': '#D97757',
  '0.5-0.7': '#C4A95B',
  '0.0-0.5': '#3B82D9',
  'unknown': '#7A7A72',
}

export const DIRECTIVE_STATUS_COLORS = {
  RECOMMENDATION: '#3B82D9',
  DIRECTIVE: '#D97757',
}

export function getTooltipStyle() {
  const root = getComputedStyle(document.documentElement)
  return {
    background: root.getPropertyValue('--brand-surface').trim() || '#FFFFFF',
    border: `1px solid ${root.getPropertyValue('--brand-border').trim() || 'rgba(20,20,19,0.10)'}`,
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 12,
    color: root.getPropertyValue('--brand-text-primary').trim() || '#141413',
    boxShadow: root.getPropertyValue('--brand-shadow').trim() || '0 2px 8px hsl(0 0% 0% / 8%)',
  }
}

export function getAxisTickStyle() {
  const root = getComputedStyle(document.documentElement)
  return {
    fill: root.getPropertyValue('--brand-text-tertiary').trim() || '#5E5D59',
    fontSize: 11,
    fontFamily: "'DM Sans', system-ui, sans-serif",
  }
}

export function getGridColor() {
  const root = getComputedStyle(document.documentElement)
  return root.getPropertyValue('--brand-border').trim() || 'rgba(20,20,19,0.10)'
}
