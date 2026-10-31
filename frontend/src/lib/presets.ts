// Home presets prefill /classes/new through query parameters. Only whole numbers in the form
// ranges and the three intensity words are taken; anything else is ignored.

export interface Preset {
  label: string
  params: Record<string, string>
}

export const PRESETS: readonly Preset[] = [
  { label: '45 min hills', params: { length: '45', climbs: '3', intensity: 'moderate' } },
  { label: '30 min intervals', params: { length: '30', sprints: '6', intensity: 'hard' } },
  { label: '60 min endurance', params: { length: '60', intensity: 'easy' } },
]

export function presetLink(preset: Preset): string {
  return `/classes/new?${new URLSearchParams(preset.params).toString()}`
}

export interface FormPreset {
  length: string
  climbs: string
  sprints: string
  races: string
  intensity: 'easy' | 'moderate' | 'hard'
}

function inRange(value: string | null, min: number, max: number): string | null {
  if (value === null || !/^\d{1,3}$/.test(value)) return null
  const n = Number(value)
  return n >= min && n <= max ? String(n) : null
}

export function readPreset(query: URLSearchParams): FormPreset {
  const intensity = query.get('intensity')
  return {
    length: inRange(query.get('length'), 10, 180) ?? '45',
    climbs: inRange(query.get('climbs'), 0, 10) ?? '0',
    sprints: inRange(query.get('sprints'), 0, 20) ?? '0',
    races: inRange(query.get('races'), 0, 5) ?? '0',
    intensity: intensity === 'easy' || intensity === 'hard' ? intensity : 'moderate',
  }
}
