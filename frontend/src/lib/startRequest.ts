// The free-text start screen's request, kept per class in this tab's session storage so "Try again"
// (a failed or stopped job, a refused hand-off) opens the start screen with the same words and
// music choice. Only what the instructor typed and picked is kept: no token, no password. Storage
// can be blocked or full, so every access is guarded and the screen then simply starts empty.

const PREFIX = 'classbeat.start-request.'

export interface KeptRequest {
  request: string
  /** The picker row's key (`musicSources.ts`), or null for "classbeat chooses". */
  music: string | null
  sport: 'cycling' | 'running'
}

export function rememberRequest(classId: string, kept: KeptRequest): void {
  try {
    window.sessionStorage.setItem(PREFIX + classId, JSON.stringify(kept))
  } catch {
    // Without storage Try again opens an empty start screen.
  }
}

export function recallRequest(classId: string): KeptRequest | null {
  try {
    const raw = window.sessionStorage.getItem(PREFIX + classId)
    if (raw === null) return null
    const parsed = JSON.parse(raw) as Partial<KeptRequest> | null
    if (typeof parsed !== 'object' || parsed === null || typeof parsed.request !== 'string') return null
    return {
      request: parsed.request,
      music: typeof parsed.music === 'string' ? parsed.music : null,
      sport: parsed.sport === 'running' ? 'running' : 'cycling',
    }
  } catch {
    return null
  }
}

/** Where "Try again" goes for a class: the start screen, which reads the kept request by class id. */
export function startAgainPath(classId: string): string {
  return `/classes/new?from=${encodeURIComponent(classId)}`
}

/** The longest request the interpret job takes (backend `MAX_REQUEST`). */
export const MAX_REQUEST = 2000

/** `prompt` with `phrase` added on a new sentence; nothing changes when the phrase is already there or would not fit. */
export function addPhrase(prompt: string, phrase: string): string {
  if (prompt.includes(phrase)) return prompt
  const base = prompt.trimEnd()
  const next = base === '' ? phrase : `${base} ${phrase}`
  return next.length > MAX_REQUEST ? prompt : next
}
