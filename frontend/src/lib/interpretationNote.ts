import type { InterpretationItem } from '../api/interpretationItems'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'
import { gameLabel, gameLength } from './games'
import type { DraftGame } from '../api/draftTypes'

// The three-part note of how a request was read (ADR 0029 section 4): "As you asked", "Not as
// asked" and "Assumed". Pure: every sentence is built from catalogue keys, a path and a reason
// code. Nothing the server or a model wrote is shown, except a `quote`, which the backend keeps
// only when it is a substring of the instructor's own request (rendered as plain text).

export interface NoteLine {
  /** Unique within its section: the path, with the position for a repeated one. */
  id: string
  text: string
  /** The instructor's own words for it, when the reading kept them. */
  quote: string | null
}

export interface Note {
  asked: NoteLine[]
  notAsked: NoteLine[]
  assumed: NoteLine[]
}

const LABELS: Record<string, CatalogueKey> = {
  sport: 'interp.path.sport',
  length_min: 'interp.path.length_min',
  intensity: 'interp.path.intensity',
  climbs: 'interp.path.climbs',
  sprints: 'interp.path.sprints',
  races: 'interp.path.races',
  ftp_test: 'interp.path.ftp_test',
  class_type: 'interp.path.class_type',
  music: 'interp.path.music',
}

const EFFORT: Record<string, CatalogueKey> = {
  easy: 'newclass.effort.easy',
  moderate: 'newclass.effort.moderate',
  hard: 'newclass.effort.hard',
}

const SPORT: Record<string, CatalogueKey> = {
  cycling: 'newclass.sport.cycling',
  running: 'newclass.sport.running',
}

const CLASS_TYPE: Record<string, CatalogueKey> = {
  plain: 'interp.classType.plain',
  interval: 'interp.classType.interval',
  hiit: 'interp.classType.hiit',
  journey: 'interp.classType.journey',
  ftp_test: 'interp.classType.ftp_test',
}

// The music item's value is which of the fixed choices won (backend `interpret_music_policy`).
const MUSIC: Record<string, CatalogueKey> = {
  override: 'interp.music.override',
  name_match: 'interp.music.name_match',
  pick: 'interp.music.pick',
  first_playlist: 'interp.music.first_playlist',
  soundless: 'interp.music.soundless',
}

export const REASONS: Record<string, CatalogueKey> = {
  sport_not_offered: 'interp.reason.sport_not_offered',
  over_plan_length: 'interp.reason.over_plan_length',
  game_on_running: 'interp.reason.game_on_running',
  plan_no_games: 'interp.reason.plan_no_games',
  game_under_10s: 'interp.reason.game_under_10s',
  length_off: 'interp.reason.length_off',
  ftp_missing: 'interp.reason.ftp_missing',
  count_off: 'interp.reason.count_off',
  game_type: 'interp.reason.game_type',
  game_missing: 'interp.reason.game_missing',
  game_placement: 'interp.reason.game_placement',
  game_duration: 'interp.reason.game_duration',
  needs_mowl_playlist: 'interp.reason.needs_mowl_playlist',
}

const GAMES: readonly string[] = ['racetrack', 'power_pull', 'fast_lane', 'overflow', 'ghost_target']

function isGame(value: unknown): value is DraftGame {
  return typeof value === 'string' && GAMES.includes(value)
}

function field(value: unknown, key: string): unknown {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>)[key] : undefined
}

function whole(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** What the item asks for, in words, or null when its value is not one the page knows. */
function valueText(item: InterpretationItem): string | null {
  const { path, value } = item
  const number = whole(value)
  switch (path) {
    case 'length_min':
      return number === null ? null : t('interp.value.minutes', { count: number })
    case 'climbs':
    case 'sprints':
    case 'races':
      return number === null ? null : String(number)
    case 'ftp_test':
      return number === null ? null : t('interp.value.ftp', { minutes: number })
    case 'intensity':
      return typeof value === 'string' && value in EFFORT ? t(EFFORT[value]!) : null
    case 'sport':
      return typeof value === 'string' && value in SPORT ? t(SPORT[value]!) : null
    case 'class_type':
      return typeof value === 'string' && value in CLASS_TYPE ? t(CLASS_TYPE[value]!) : null
    case 'music':
      return typeof value === 'string' && value in MUSIC ? t(MUSIC[value]!) : null
    default:
      return null
  }
}

/** A game item: its name (or "A game" for "any") and, if asked, how long. */
function gameText(item: InterpretationItem): string {
  const type = field(item.value, 'type')
  const name = isGame(type) ? gameLabel(type) : t('interp.game.any')
  const sec = whole(field(item.value, 'duration_sec'))
  return sec === null ? name : t('interp.game.withLength', { game: name, length: gameLength(sec) })
}

function what(item: InterpretationItem): string {
  if (item.path.startsWith('games.')) return gameText(item)
  const label = Object.hasOwn(LABELS, item.path) ? t(LABELS[item.path]!) : t('interp.path.other')
  const value = valueText(item)
  return value === null ? label : t('interp.line', { what: label, value })
}

function line(item: InterpretationItem, id: string): NoteLine {
  const base = what(item)
  if (item.origin !== 'not_as_asked') return { id, text: base, quote: item.quote }
  const reason = item.reasonCode !== null && Object.hasOwn(REASONS, item.reasonCode) ? REASONS[item.reasonCode]! : 'interp.reason.other'
  return { id, text: t('interp.line.notAsked', { what: base, why: t(reason) }), quote: item.quote }
}

/** The items sorted into the three parts, each line worded from the catalogue. */
export function interpretationNote(items: readonly InterpretationItem[]): Note {
  const note: Note = { asked: [], notAsked: [], assumed: [] }
  items.forEach((item, at) => {
    const id = `${item.path}.${String(at)}`
    const target = item.origin === 'explicit' ? note.asked : item.origin === 'not_as_asked' ? note.notAsked : note.assumed
    target.push(line(item, id))
  })
  return note
}
