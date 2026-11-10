import type { DraftGame, DraftSegment } from '../api/draftTypes'
import type { CatalogueKey } from '../i18n/en'
import type { SlotId } from '../components/imagery/imageSlots'
import { t } from '../i18n/t'

// MOWL game names and illustrations (ADR 0029 section 3). Pure: nothing here reads the network
// or the page. `ghost_target` is a legacy value, read-only: it has a name and no illustration.

const SLOT: Record<Exclude<DraftGame, 'ghost_target'>, SlotId> = {
  racetrack: 'gameRacetrack',
  power_pull: 'gamePowerPull',
  fast_lane: 'gameFastLane',
  overflow: 'gameOverflow',
}

/** The game's name in the active language; the legacy game says it is no longer offered. */
export function gameLabel(game: DraftGame): string {
  return t(`class.game.${game}` satisfies CatalogueKey)
}

/** The illustration slot of a game, or null for the legacy game (it is not shown as a choice). */
export function gameSlot(game: DraftGame): SlotId | null {
  return game === 'ghost_target' ? null : SLOT[game]
}

/** "2 min", "45 s" or "1 min 30 s". */
export function gameLength(sec: number): string {
  const minutes = Math.floor(sec / 60)
  const seconds = Math.round(sec % 60)
  if (minutes === 0) return t('class.game.lengthSec', { seconds })
  return seconds === 0 ? t('class.game.lengthMin', { minutes }) : t('class.game.lengthMinSec', { minutes, seconds })
}

/** The accessible name of a game marker, for example "Power Pull game, 2 min". */
export function gameMarkerName(game: DraftGame, sec: number): string {
  return t('class.game.marker', { game: gameLabel(game), length: gameLength(sec) })
}

/** The different games of one segment, in the order they first appear. */
export function segmentGames(segment: DraftSegment): DraftGame[] {
  const seen: DraftGame[] = []
  for (const interval of segment.intervals) {
    if (interval.game !== undefined && !seen.includes(interval.game)) seen.push(interval.game)
  }
  return seen
}

/** True when any interval is a game. A running class never has one, so it shows no game UI. */
export function hasGame(segments: DraftSegment[]): boolean {
  return segments.some((s) => s.intervals.some((i) => i.game !== undefined))
}
