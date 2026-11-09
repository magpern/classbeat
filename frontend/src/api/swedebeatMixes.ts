import { ApiError, get } from './client'
import { flag, list, object, optionalCount, text } from './decode'
import { isFeatureOff } from './spotify'

// The user's Swedebeat mixes as MOWL lists them (ADR 0016 section 3, GET /api/music/swedebeat-mixes).
// Names and lengths only; never a URL or token.

export interface SwedebeatMix {
  mixId: string
  name: string
  durationSeconds: number | null
  analysed: boolean
}

function decodeMix(value: unknown): SwedebeatMix {
  const item = object(value, 'mix')
  const duration = optionalCount(item, 'duration_seconds')
  return {
    mixId: text(item, 'mix_id'),
    name: text(item, 'name'),
    durationSeconds: duration ?? null,
    analysed: flag(item, 'analysed'),
  }
}

/** The mixes, or `null` when the effective `swedebeat_enabled` flag is off. */
export async function getSwedebeatMixes(signal?: AbortSignal): Promise<SwedebeatMix[] | null> {
  try {
    const body = object(await get('/api/music/swedebeat-mixes', signal), 'mixes')
    return list(body, 'mixes').map(decodeMix)
  } catch (error) {
    if (isFeatureOff(error)) return null
    throw error
  }
}

/** Plain sentences for list failures; never a provider message or reason code. */
export function swedebeatMixListError(error: unknown): string {
  if (error instanceof ApiError && error.reason !== null && error.status !== 409) {
    const known: Record<string, string> = {
      upstream_unavailable: 'MOWL could not be reached. Try again in a few minutes.',
      key_unavailable: 'MOWL could not be reached. Try again in a few minutes.',
      rate_limited: 'MOWL is busy. Try again in a few minutes.',
      outbound_blocked: 'Your Swedebeat mixes could not be loaded right now. Try again in a few minutes.',
    }
    const sentence = Object.hasOwn(known, error.reason) ? known[error.reason] : undefined
    if (sentence !== undefined) return sentence
  }
  return 'Your Swedebeat mixes could not be loaded from MOWL. Try again in a few minutes.'
}
