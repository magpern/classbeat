import { count, flag, isObject, object, optionalCount, optionalFlag, optionalText, text, type JsonObject } from './decode'
import type { DraftContent } from './draftTypes'
import type { InterpretationItem } from './interpretationItems'

// The revision types of the class API and their decoders (split out of classes.ts, issue 876).

export interface RevisionSummary {
  id: string
  number: number
  parentId: string | null
  parentNumber: number | null
  /** `design_draft` or `revise_draft`. */
  origin: string
  createdAt: string
  isCurrent: boolean
  segmentCount: number
  intervalCount: number
  totalSec: number
}

export function decodeRevisionSummary(item: JsonObject): RevisionSummary {
  const summary = object(item.summary, 'summary')
  return {
    id: text(item, 'id'),
    number: count(item, 'number'),
    parentId: optionalText(item, 'parent_id'),
    parentNumber: optionalCount(item, 'parent_number'),
    origin: text(item, 'origin'),
    createdAt: text(item, 'created_at'),
    isCurrent: flag(item, 'is_current'),
    segmentCount: count(summary, 'segments'),
    intervalCount: count(summary, 'intervals'),
    totalSec: count(summary, 'total_sec'),
  }
}

/** One pattern note (ADR 0017 section 4): ids and numbers; the wording is keyed by `rule`. */
export interface RuleHit {
  rule: string
  severity: 'info' | 'warn'
  metric: string
  value: number
  bounds: [number | null, number | null]
  explainedByBrief: boolean
}

function bound(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** Absent or malformed `rule_hits` is an empty list: a note is never worth an error. */
export function decodeRuleHits(body: JsonObject): RuleHit[] {
  const raw = body.rule_hits
  if (!Array.isArray(raw)) return []
  const hits: RuleHit[] = []
  for (const entry of raw) {
    if (!isObject(entry)) continue
    const value = number(entry, 'value')
    const bounds = Array.isArray(entry.bounds) ? entry.bounds : []
    if (typeof entry.rule !== 'string' || value === null) continue
    hits.push({
      rule: entry.rule,
      severity: entry.severity === 'warn' ? 'warn' : 'info',
      metric: typeof entry.metric === 'string' ? entry.metric : '',
      value,
      bounds: [bound(bounds[0]), bound(bounds[1])],
      explainedByBrief: optionalFlag(entry, 'explained_by_brief') ?? false,
    })
  }
  return hits
}

export interface Revision extends RevisionSummary, DraftContent {
  unknownBpm: { index: number; title: string }[]
  /** ADR 0012 section 7: the backend's flag, set when the version has a zone 7 interval. */
  tssApproximate: boolean
  /** ADR 0017 section 4: pattern notes under today's rules (at most 5, dismissed ones left out). */
  ruleHits: RuleHit[]
  /** The recorded rules differ from today's: the block says "Notes follow current rules". */
  notesUseCurrentRules: boolean
  /** The content exactly as the server sent it: what the version hash is taken over. */
  raw: unknown
  /** How the request behind this version was read (ADR 0029 section 4); null when none was recorded. */
  interpretation: InterpretationItem[] | null
}

/** The backend's change summary of one answer (classbeat.design.diff); every part is optional. */
export interface ChangeSummary {
  segmentsAdded: number | null
  segmentsRemoved: number | null
  intervalsAdded: number | null
  intervalsRemoved: number | null
  intervalsChanged: number | null
  durationBefore: number | null
  durationAfter: number | null
  tssBefore: number | null
  tssAfter: number | null
  textChanged: boolean
  musicChanged: boolean
}

function number(source: unknown, key: string): number | null {
  if (!isObject(source)) return null
  const value = source[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function decodeChange(value: unknown): ChangeSummary | null {
  if (!isObject(value)) return null
  return {
    segmentsAdded: number(value.segments, 'added'),
    segmentsRemoved: number(value.segments, 'removed'),
    intervalsAdded: number(value.intervals, 'added'),
    intervalsRemoved: number(value.intervals, 'removed'),
    intervalsChanged: number(value.intervals, 'changed'),
    durationBefore: number(value.duration_sec, 'before'),
    durationAfter: number(value.duration_sec, 'after'),
    tssBefore: number(value.tss, 'before'),
    tssAfter: number(value.tss, 'after'),
    textChanged: value.text_changed === true,
    musicChanged: value.music_changed === true,
  }
}
