import type { DraftGame } from '../../api/draftTypes'
import type { CatalogueKey } from '../../i18n/en'

// The "Ideas for your class" phrases (pure data). Tapping one adds its sentence to the request;
// nothing is counted and nothing is required.

export type IdeaGroup = 'climbs' | 'sprints' | 'intervals' | 'games'

export const GROUPS: readonly IdeaGroup[] = ['climbs', 'sprints', 'intervals', 'games']

/** The group's name in the row. */
export const GROUP_LABEL: Record<IdeaGroup, CatalogueKey> = {
  climbs: 'start.ideas.climbs',
  sprints: 'start.ideas.sprints',
  intervals: 'start.ideas.intervals',
  games: 'start.ideas.games',
}

/** Sentences for the three plain groups; each reads as a sentence on its own. */
export const PHRASES: Record<Exclude<IdeaGroup, 'games'>, readonly CatalogueKey[]> = {
  climbs: ['start.phrase.climbs.long', 'start.phrase.climbs.finish', 'start.phrase.climbs.seated'],
  sprints: ['start.phrase.sprints.six', 'start.phrase.sprints.second', 'start.phrase.sprints.short'],
  intervals: ['start.phrase.intervals.onoff', 'start.phrase.intervals.build', 'start.phrase.intervals.pyramid'],
}

/** The starter ideas under the prompt. */
export const STARTERS: readonly CatalogueKey[] = ['start.starter.hills', 'start.starter.intervals', 'start.starter.endurance']

/** The four games of the game cards (never the legacy game), with MOWL's own one-line description of each. */
export const GAME_CARDS: readonly { game: Exclude<DraftGame, 'ghost_target'>; article: CatalogueKey }[] = [
  { game: 'racetrack', article: 'start.game.article.racetrack' },
  { game: 'power_pull', article: 'start.game.article.power_pull' },
  { game: 'fast_lane', article: 'start.game.article.fast_lane' },
  { game: 'overflow', article: 'start.game.article.overflow' },
]
