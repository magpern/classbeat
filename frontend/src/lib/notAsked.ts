import type { InterpretationItem } from '../api/interpretationItems'

/**
 * The Not as asked items of the version the instructor is about to approve or save, from the
 * sources that hold them (the version's own reading, a song list's unmet items). Pure; an item that
 * two sources both hold shows once.
 */
export function notAskedOf(...sources: readonly (readonly InterpretationItem[] | null | undefined)[]): InterpretationItem[] {
  const seen = new Set<string>()
  const out: InterpretationItem[] = []
  for (const source of sources) {
    for (const item of source ?? []) {
      if (item.origin !== 'not_as_asked') continue
      const key = `${item.path}|${item.reasonCode ?? ''}`
      if (seen.has(key)) continue
      seen.add(key)
      out.push(item)
    }
  }
  return out
}
