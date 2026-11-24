import type { InterpretationItem } from '../api/interpretationItems'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'

// The shape of the ride in the reading (ADR 0029 amendment of 2026-11-24, issue #1494): one item
// per section, path `shape.N` (0-based, in the request's order), whose value is a closed object.
// Pure: every word comes from the catalogue; the section's values are checked against the closed
// sets below and anything else is left out. The instructor's own words are the item's `quote`.

type Kind = 'warmup' | 'climb' | 'efforts' | 'flat' | 'recovery' | 'cooldown'

export interface Section {
  kind: Kind
  trend?: 'steady' | 'rising' | 'falling'
  effort?: 'easy' | 'moderate' | 'hard'
  count?: { min: number; max: number }
  length?: 'short' | 'long'
  load?: 'light' | 'heavy'
  minutes?: number
  /** A `shape_off` item: how many the version has (what was found), when the record says. */
  found?: number
}

export interface ShapeLine {
  id: string
  text: string
  quote: string | null
}

const KIND: Record<Kind, CatalogueKey> = {
  warmup: 'shape.kind.warmup',
  climb: 'shape.kind.climb',
  efforts: 'shape.kind.efforts',
  flat: 'shape.kind.flat',
  recovery: 'shape.kind.recovery',
  cooldown: 'shape.kind.cooldown',
}
const TREND: Record<string, CatalogueKey> = {
  steady: 'shape.trend.steady',
  rising: 'shape.trend.rising',
  falling: 'shape.trend.falling',
}
const EFFORT: Record<string, CatalogueKey> = {
  easy: 'shape.effort.easy',
  moderate: 'shape.effort.moderate',
  hard: 'shape.effort.hard',
}
const LENGTH: Record<string, CatalogueKey> = { short: 'shape.length.short', long: 'shape.length.long' }
const LOAD: Record<string, CatalogueKey> = { light: 'shape.load.light', heavy: 'shape.load.heavy' }

function member<K extends string>(value: unknown, allowed: Record<string, unknown>): K | undefined {
  return typeof value === 'string' && Object.hasOwn(allowed, value) ? (value as K) : undefined
}

function whole(value: unknown, low: number, high: number): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) && value >= low && value <= high ? value : undefined
}

/** The section a `shape.N` value describes, or null when it is not a closed section. */
export function sectionOf(value: unknown): Section | null {
  if (typeof value !== 'object' || value === null) return null
  const v = value as Record<string, unknown>
  const kind = member<Kind>(v.kind, KIND)
  if (kind === undefined) return null
  const section: Section = { kind }
  const trend = member<NonNullable<Section['trend']>>(v.trend, TREND)
  const effort = member<NonNullable<Section['effort']>>(v.effort, EFFORT)
  const length = member<NonNullable<Section['length']>>(v.length, LENGTH)
  const load = member<NonNullable<Section['load']>>(v.load, LOAD)
  const minutes = whole(v.minutes, 1, 120)
  const found = whole(v.found, 0, 999)
  const count = typeof v.count === 'object' && v.count !== null ? (v.count as Record<string, unknown>) : null
  const min = whole(count?.min, 1, 20)
  const max = whole(count?.max, 1, 20)
  if (trend !== undefined) section.trend = trend
  if (effort !== undefined) section.effort = effort
  if (length !== undefined) section.length = length
  if (load !== undefined) section.load = load
  if (minutes !== undefined) section.minutes = minutes
  if (found !== undefined) section.found = found
  if (min !== undefined && max !== undefined && min <= max) section.count = { min, max }
  return section
}

/** The index of a `shape.N` path, or null. */
export function shapeIndex(path: string): number | null {
  const match = /^shape\.([0-9]{1,2})$/.exec(path)
  return match === null ? null : Number(match[1])
}

const COUNTED: readonly Kind[] = ['climb', 'efforts']

function rangeText(count: { min: number; max: number }): string {
  return count.min === count.max ? String(count.min) : t('shape.range', { min: count.min, max: count.max })
}

/** "5 to 7 efforts", or null when the section has no count to name. */
function askedText(section: Section): string | null {
  if (section.count === undefined || !COUNTED.includes(section.kind)) return null
  return `${rangeText(section.count)} ${t(KIND[section.kind], { count: section.count.max })}`
}

function capitalised(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** The section in short closed words: "5 to 7 short, heavy efforts, hard". */
export function describeSection(section: Section): string {
  const counted = COUNTED.includes(section.kind)
  const many = section.count?.max ?? (section.kind === 'climb' ? 1 : 2)
  const adjectives = counted
    ? [
        section.length === undefined ? null : t(LENGTH[section.length]!, { count: many }),
        section.load === undefined ? null : t(LOAD[section.load]!, { count: many }),
      ].filter((word) => word !== null)
    : []
  const count = counted && section.count !== undefined ? rangeText(section.count) : null
  const noun = t(KIND[section.kind], { count: many })
  const head = capitalised([count, adjectives.join(', '), noun].filter((word) => word !== null && word !== '').join(' '))
  const tail = [
    section.trend === undefined ? null : t(TREND[section.trend]!),
    section.effort === undefined ? null : t(EFFORT[section.effort]!),
    section.minutes === undefined ? null : t('shape.minutes', { minutes: section.minutes }),
  ].filter((word) => word !== null)
  return [head, ...tail].join(', ')
}

/** Part of the ride as read: `shape.N`, explicit or inferred, with a closed value. */
export function isShapeReading(item: InterpretationItem): boolean {
  return item.origin !== 'not_as_asked' && shapeIndex(item.path) !== null && sectionOf(item.value) !== null
}

function plans(sections: Section[], items: readonly InterpretationItem[]): string[] {
  const kinds = new Set(sections.map((s) => s.kind))
  const indexes = items.map((i) => shapeIndex(i.path)).sort((a, b) => (a ?? 0) - (b ?? 0))
  // A revision's items are only the sections it touched: say what classbeat adds or chooses only for
  // a reading of the whole ride (every section from 0 on, and the instructor's own words kept).
  const complete = indexes.every((at, n) => at === n) && items.some((i) => i.quote !== null)
  if (!complete) return []
  const out: string[] = []
  const ranged = sections.some((s) => s.count !== undefined && s.count.min < s.count.max)
  const open = sections.some((s) => COUNTED.includes(s.kind) && (s.count === undefined || s.length === undefined))
  if (ranged) out.push(t('shape.chooses.range'))
  else if (open) out.push(t('shape.chooses.open'))
  const warmup = kinds.has('warmup')
  const cooldown = kinds.has('cooldown')
  if (!warmup && !cooldown) out.push(t('shape.adds.both'))
  else if (!warmup) out.push(t('shape.adds.warmup'))
  else if (!cooldown) out.push(t('shape.adds.cooldown'))
  return out
}

/** The ride's sections in order, and the lines on what classbeat chooses and adds. */
export function shapeReading(items: readonly InterpretationItem[]): { lines: ShapeLine[]; notes: string[] } {
  const found = items.filter(isShapeReading).map((item) => ({ item, at: shapeIndex(item.path) ?? 0, section: sectionOf(item.value)! }))
  found.sort((a, b) => a.at - b.at)
  return {
    lines: found.map(({ item, at, section }) => ({
      id: `${item.path}.${String(at)}`,
      text: t('shape.line', { n: at + 1, text: describeSection(section) }),
      quote: item.quote,
    })),
    notes: plans(
      found.map((f) => f.section),
      found.map((f) => f.item),
    ),
  }
}

/** "Section 3" with what was asked, for a `shape.N` item that could not be done as asked. */
export function shapeWhat(item: InterpretationItem): string {
  const at = shapeIndex(item.path)
  const section = at === null ? t('shape.section.other') : t('shape.section', { n: at + 1 })
  const value = sectionOf(item.value)
  return value === null ? section : t('shape.what', { section, text: describeSection(value) })
}

/**
 * The whole sentence of a `shape_off` item that knows what was found ("Section 3: you asked for 7
 * efforts; this version has 5"), or null: the generic line is used then.
 */
export function shapeOffText(item: InterpretationItem): string | null {
  const at = shapeIndex(item.path)
  const section = sectionOf(item.value)
  if (item.reasonCode !== 'shape_off' || at === null || section === null) return null
  const asked = askedText(section)
  const found = item.found ?? section.found
  if (asked === null || found === undefined || found === null) return null
  return t('shape.off', { section: t('shape.section', { n: at + 1 }), asked, found })
}

/**
 * An untagged draft records one `shape_off` on `shape.0` without a count: the version does not
 * follow the ride's parts at all, so no single section is named.
 *
 * Known limit (backend `agent/shape_check.py`, #1510 item 13): `recorded()` gives every shape miss the
 * same reason `shape_off`, so a draft whose only miss is section 0 (`shape_missing`: no segment tagged 0,
 * or `shape_order`: a section-0 segment after a later section's) records the same single item. The
 * record carries nothing to tell the two apart; only a backend marker can.
 */
export function isUntagged(items: readonly InterpretationItem[]): boolean {
  const off = items.filter((i) => i.origin === 'not_as_asked' && i.reasonCode === 'shape_off')
  const [only] = off
  return off.length === 1 && only !== undefined && only.path === 'shape.0' && (only.found ?? null) === null
}
