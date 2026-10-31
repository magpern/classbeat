// Small display helpers shared by the admin pages. Everything shown is plain text.

/** `2026-10-31T12:00:00Z` as a day in the admin's locale; the raw value when it does not parse. */
export function dayOf(value: string | null): string {
  if (value === null) return ''
  const when = new Date(value)
  return Number.isNaN(when.getTime())
    ? value
    : when.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function sourceLabel(source: string | null): string {
  switch (source) {
    case null:
      return ''
    case 'grant':
      return ' (granted)'
    case 'code':
      return ' (code)'
    case 'trial':
      return ' (trial)'
    default:
      return ` (${source})`
  }
}

/** A date input's `YYYY-MM-DD` as the end of that day in the admin's time zone, as ISO 8601. */
export function endOfDay(day: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null
  const when = new Date(`${day}T23:59:59`)
  return Number.isNaN(when.getTime()) ? null : when.toISOString()
}

export const PLANS = [
  { id: 'instructor', name: 'Instructor' },
  { id: 'pro', name: 'Pro' },
  { id: 'free', name: 'Free' },
] as const

export const FEATURES = [{ id: 'song_picking', name: 'Song picking' }] as const
