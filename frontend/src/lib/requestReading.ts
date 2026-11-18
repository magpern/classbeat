import type { ClassMessage } from '../api/classes'
import type { InterpretationItem } from '../api/interpretationItems'
import type { Revision } from '../api/revisionTypes'

/**
 * The start of a class's chat (moved out of `pages/ClassPage.tsx` unchanged, #1185): the first
 * message of a class made on the start screen is the request itself (no base version).
 */
export function requestOf(messages: readonly ClassMessage[]): ClassMessage | null {
  const first = messages.at(0)
  return first?.role === 'user' && first.baseRevisionId === null ? first : null
}

/**
 * The reading shown under the request. A revise version keeps the reading of its change request,
 * not of the request above: only a designed version's reading belongs under it (a revised-only
 * view shows none, by design); else the hand-off's.
 */
export function readingOf(
  shown: Revision | null,
  handoffItems: InterpretationItem[] | null,
): InterpretationItem[] | null {
  const designed = shown !== null && shown.origin === 'design_draft'
  return (designed ? shown.interpretation : null) ?? handoffItems
}
