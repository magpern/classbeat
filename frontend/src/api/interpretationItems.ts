import { isObject } from './decode'

// How a request was read (ADR 0029 section 4; backend `agent/interpretation_record.py`): closed
// items of a path, an origin, an optional reason code and a small value. Server text is data: the
// wording of every item comes from the catalogue by path and code (`lib/interpretationNote.ts`).
// Only a `quote` is shown as words, and only as plain text: it is a substring of the instructor's
// own request.

export type ItemOrigin = 'explicit' | 'inferred' | 'default' | 'not_as_asked'

export interface InterpretationItem {
  /** A path of the brief: `length_min`, `climbs`, `music`, `games.0` and the like. */
  path: string
  origin: ItemOrigin
  /** Set for `not_as_asked` items only. */
  reasonCode: string | null
  value: unknown
  quote: string | null
}

const ORIGINS: readonly string[] = ['explicit', 'inferred', 'default', 'not_as_asked']

/** The items of a response, or null when there is none; an item that does not check is left out. */
export function decodeInterpretationItems(source: unknown): InterpretationItem[] | null {
  if (!Array.isArray(source)) return null
  const items: InterpretationItem[] = []
  for (const entry of source as unknown[]) {
    if (!isObject(entry) || typeof entry.path !== 'string' || typeof entry.origin !== 'string') continue
    if (!ORIGINS.includes(entry.origin)) continue
    items.push({
      path: entry.path,
      origin: entry.origin as ItemOrigin,
      reasonCode: typeof entry.reason_code === 'string' ? entry.reason_code : null,
      value: entry.value ?? null,
      quote: typeof entry.quote === 'string' && entry.quote !== '' ? entry.quote : null,
    })
  }
  return items
}
