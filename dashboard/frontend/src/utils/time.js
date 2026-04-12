const TZ = 'America/Chicago'

export function formatTimestamp(ts) {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString('en-US', { timeZone: TZ })
  } catch {
    return '—'
  }
}

export function formatTime(ts) {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleTimeString('en-US', { timeZone: TZ })
  } catch {
    return '—'
  }
}
