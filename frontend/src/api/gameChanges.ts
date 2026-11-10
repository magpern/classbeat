import { isObject } from './decode'
import type { DraftGame } from './draftTypes'
import { gameLabel } from '../lib/games'
import { t } from '../i18n/t'

// Game changes between two versions, as the compare answer lists them (`game_changes`; issue 876).

/** One segment's game before and after; null means no game there. */
export interface GameChange {
  /** The segment, counted from 1. */
  segment: number
  before: DraftGame | null
  after: DraftGame | null
}

const GAMES: readonly unknown[] = ['racetrack', 'power_pull', 'fast_lane', 'overflow', 'ghost_target']

const game = (value: unknown): DraftGame | null => (GAMES.includes(value) ? (value as DraftGame) : null)

/** An absent or malformed list is no changes: a note on games is never worth an error. */
export function decodeGameChanges(value: unknown): GameChange[] {
  if (!Array.isArray(value)) return []
  const out: GameChange[] = []
  for (const entry of value) {
    if (!isObject(entry) || typeof entry.segment !== 'number' || !Number.isInteger(entry.segment)) continue
    const change = { segment: entry.segment, before: game(entry.before), after: game(entry.after) }
    if (change.before !== change.after) out.push(change)
  }
  return out
}

/** One sentence per changed game, in the page's words. */
export function gameChangeLines(changes: GameChange[]): string[] {
  return changes.map(({ segment, before, after }) => {
    if (before !== null && after !== null) {
      return t('class.compare.gameChanged', { segment, from: gameLabel(before), to: gameLabel(after) })
    }
    if (after !== null) return t('class.compare.gameAdded', { segment, game: gameLabel(after) })
    return t('class.compare.gameRemoved', { segment, game: gameLabel(before as DraftGame) })
  })
}
