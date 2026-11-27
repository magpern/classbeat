import type { InterpretationItem } from '../api/interpretationItems'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'
import { gameLabel, gameLength } from './games'
import type { DraftGame } from '../api/draftTypes'
import { getActiveLocale } from '../i18n/locales'
import { isShapeReading, isUntagged, shapeOffText, shapeReading, shapeWhat } from './shapeNote'

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
  /** The ride's sections as read, in order (`shape.N`), and what classbeat chooses and adds. */
  shape: NoteLine[]
  shapeNotes: string[]
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
  // The music taste (issue #1133): a path per kind; eras and style words carry their number.
  music_era: 'interp.path.music_era',
  music_language: 'interp.path.music_language',
  music_genre: 'interp.path.music_genre',
  music_hits: 'interp.path.music_hits',
  music_taste: 'interp.path.music_taste',
  music_length: 'interp.path.music_length',
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
  plan_no_ftp_tests: 'interp.reason.plan_no_ftp_tests',
  game_under_10s: 'interp.reason.game_under_10s',
  length_off: 'interp.reason.length_off',
  ftp_missing: 'interp.reason.ftp_missing',
  count_off: 'interp.reason.count_off',
  game_type: 'interp.reason.game_type',
  game_missing: 'interp.reason.game_missing',
  game_placement: 'interp.reason.game_placement',
  game_duration: 'interp.reason.game_duration',
  needs_mowl_playlist: 'interp.reason.needs_mowl_playlist',
  two_sports: 'interp.reason.two_sports',
  needs_spotify: 'interp.reason.needs_spotify',
  song_picking_unavailable: 'interp.reason.song_picking_unavailable',
  taste_needs_pick: 'interp.reason.taste_needs_pick',
  taste_on_revise: 'interp.reason.taste_on_revise',
  era_too_few: 'interp.reason.era_too_few',
  songs_outside_eras: 'interp.reason.songs_outside_eras',
  songs_too_short: 'interp.reason.songs_too_short',
  shape_off: 'interp.reason.shape_off',
  shape_untagged: 'interp.reason.shape_untagged',
  cycling_only: 'interp.reason.cycling_only',
  shape_too_large: 'interp.reason.shape_too_large',
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

/** A language's name in the page's language (`sv` is "Swedish"), or the code when unknown. */
function languageName(code: string): string {
  try {
    return new Intl.DisplayNames([getActiveLocale()], { type: 'language' }).of(code) ?? code
  } catch {
    return code
  }
}

/** The kind of a path: `music_era.1` is `music_era`, `length_min` stays. */
function kindOf(path: string): string {
  return path.startsWith('music_') ? path.replace(/\.[0-9]+$/, '') : path
}

/** A music taste item's value in words, or null. */
function tasteText(kind: string, value: unknown): string | null {
  const from = whole(field(value, 'from'))
  const to = whole(field(value, 'to'))
  switch (kind) {
    case 'music_era':
      return from === null || to === null ? null : t('interp.value.years', { from, to })
    case 'music_language':
      return typeof value === 'string' && /^[a-z]{2}$/.test(value) ? languageName(value) : null
    case 'music_length': {
      const total = whole(field(value, 'total_min'))
      const target = whole(field(value, 'target_min'))
      return total === null || target === null ? null : t('interp.value.songsLength', { total, target })
    }
    default:
      return null
  }
}

/** What the item asks for, in words, or null when its value is not one the page knows. */
function valueText(item: InterpretationItem): string | null {
  const { path, value } = item
  const number = whole(value)
  if (path.startsWith('music_')) return tasteText(kindOf(path), value)
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
  if (item.path.startsWith('shape.')) return shapeWhat(item)
  const kind = kindOf(item.path)
  const label = Object.hasOwn(LABELS, kind) ? t(LABELS[kind]!) : t('interp.path.other')
  const value = valueText(item)
  return value === null ? label : t('interp.line', { what: label, value })
}

function line(item: InterpretationItem, id: string, untagged: boolean): NoteLine {
  const base = what(item)
  if (item.origin !== 'not_as_asked') return { id, text: base, quote: item.quote }
  const off = untagged && item.reasonCode === 'shape_untagged' ? t('shape.untagged') : shapeOffText(item)
  if (off !== null) return { id, text: off, quote: item.quote }
  const reason = item.reasonCode !== null && Object.hasOwn(REASONS, item.reasonCode) ? REASONS[item.reasonCode]! : 'interp.reason.other'
  return { id, text: t('interp.line.notAsked', { what: base, why: t(reason) }), quote: item.quote }
}

/** The items sorted into the three parts, each line worded from the catalogue. */
export function interpretationNote(items: readonly InterpretationItem[]): Note {
  const { lines, notes } = shapeReading(items)
  const untagged = isUntagged(items)
  const note: Note = { shape: lines, shapeNotes: notes, asked: [], notAsked: [], assumed: [] }
  items.forEach((item, at) => {
    if (isShapeReading(item)) return
    const id = `${item.path}.${String(at)}`
    const target = item.origin === 'explicit' ? note.asked : item.origin === 'not_as_asked' ? note.notAsked : note.assumed
    target.push(line(item, id, untagged))
  })
  return note
}
