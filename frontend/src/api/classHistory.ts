import { ApiError, get } from './client'
import { count, flag, isObject, list, object, optionalText, text, type JsonObject } from './decode'
import { clock } from '../lib/workout'

// The class history (ADR 0007 amendment of 2026-11-03, part H; backend classbeat/classes/history.py):
// the timeline of a class's versions and the comparison of two. Server text (a chat request, a
// class title) is data, rendered as text by React, never as HTML.

export type SavedState = 'live' | 'detached' | 'none'

export interface DuplicatedFrom {
  /** `null` when that class is gone. */
  classId: string | null
  classTitle: string | null
  number: number | null
}

export interface TimelineEntry {
  id: string
  number: number
  parentNumber: number | null
  /** `design_draft`, `revise_draft`, `duplicate`, ... */
  origin: string
  createdAt: string
  isCurrent: boolean
  contentHash: string
  segmentCount: number
  intervalCount: number
  totalSec: number
  tss: number | null
  tssApproximate: boolean
  /** The user's own chat request that asked for this version (a change only). */
  request: string | null
  saved: SavedState
  fromSongs: boolean
  duplicatedFrom: DuplicatedFrom | null
}

export interface History {
  sport: 'cycling' | 'running'
  /** The class has its own brief and music, which Change it needs (a copy on picked songs has not). */
  canChange: boolean
  revisions: TimelineEntry[]
  /** Versions that no longer validate: left out of `revisions` and only counted by the server. */
  unreadable: number
}

function number(source: JsonObject, key: string): number {
  const value = source[key]
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`bad ${key}`)
  return value
}

function optionalNumber(source: JsonObject, key: string): number | null {
  return source[key] === null || source[key] === undefined ? null : number(source, key)
}

function optionalCountOf(source: JsonObject, key: string): number | null {
  return source[key] === null || source[key] === undefined ? null : count(source, key)
}

function decodeSaved(value: unknown): SavedState {
  return value === 'live' || value === 'detached' ? value : 'none'
}

function decodeDuplicated(value: unknown): DuplicatedFrom | null {
  if (!isObject(value)) return null
  return {
    classId: optionalText(value, 'class_id'),
    classTitle: optionalText(value, 'class_title'),
    number: optionalCountOf(value, 'number'),
  }
}

function decodeEntry(item: JsonObject): TimelineEntry {
  const summary = object(item.summary, 'summary')
  return {
    id: text(item, 'id'),
    number: count(item, 'number'),
    parentNumber: optionalCountOf(item, 'parent_number'),
    origin: text(item, 'origin'),
    createdAt: text(item, 'created_at'),
    isCurrent: flag(item, 'is_current'),
    contentHash: text(item, 'content_hash'),
    segmentCount: count(summary, 'segments'),
    intervalCount: count(summary, 'intervals'),
    totalSec: count(summary, 'total_sec'),
    tss: optionalNumber(item, 'tss'),
    tssApproximate: flag(item, 'tss_approximate'),
    request: optionalText(item, 'request'),
    saved: decodeSaved(item.saved),
    fromSongs: flag(item, 'from_songs'),
    duplicatedFrom: decodeDuplicated(item.duplicated_from),
  }
}

function base(classId: string): string {
  return `/api/classes/${encodeURIComponent(classId)}/history`
}

export async function getHistory(classId: string, signal?: AbortSignal): Promise<History> {
  const body = object(await get(base(classId), signal), 'history')
  return {
    sport: body.sport === 'running' ? 'running' : 'cycling',
    canChange: flag(body, 'can_change'),
    revisions: list(body, 'revisions').map((r) => decodeEntry(object(r, 'revision'))),
    unreadable: optionalCountOf(body, 'unreadable') ?? 0,
  }
}

export interface Comparison {
  fromNumber: number
  toNumber: number
  segmentsAdded: number
  segmentsRemoved: number
  intervalsAdded: number
  intervalsRemoved: number
  intervalsChanged: number
  secondsBefore: number
  secondsAfter: number
  tssBefore: number
  tssAfter: number
  textChanged: boolean
  unknownBpmBefore: number
  unknownBpmAfter: number
  unsureBefore: number
  unsureAfter: number
  sameSource: boolean
  sameFingerprint: boolean
}

export async function compareRevisions(
  classId: string,
  fromId: string,
  toId: string,
  signal?: AbortSignal,
): Promise<Comparison> {
  const query = `?from=${encodeURIComponent(fromId)}&to=${encodeURIComponent(toId)}`
  const body = object(await get(`${base(classId)}/compare${query}`, signal), 'comparison')
  const segments = object(body.segments, 'segments')
  const intervals = object(body.intervals, 'intervals')
  const duration = object(body.duration_sec, 'duration_sec')
  const tss = object(body.tss, 'tss')
  const warnings = object(body.warnings, 'warnings')
  const before = object(warnings.from, 'from')
  const after = object(warnings.to, 'to')
  const music = object(body.music, 'music')
  return {
    fromNumber: count(body, 'from_number'),
    toNumber: count(body, 'to_number'),
    segmentsAdded: count(segments, 'added'),
    segmentsRemoved: count(segments, 'removed'),
    intervalsAdded: count(intervals, 'added'),
    intervalsRemoved: count(intervals, 'removed'),
    intervalsChanged: count(intervals, 'changed'),
    secondsBefore: number(duration, 'before'),
    secondsAfter: number(duration, 'after'),
    tssBefore: number(tss, 'before'),
    tssAfter: number(tss, 'after'),
    textChanged: flag(body, 'text_changed'),
    unknownBpmBefore: count(before, 'unknown_bpm'),
    unknownBpmAfter: count(after, 'unknown_bpm'),
    unsureBefore: count(before, 'unsure_beat_one'),
    unsureAfter: count(after, 'unsure_beat_one'),
    sameSource: flag(music, 'same_source'),
    sameFingerprint: flag(music, 'same_fingerprint'),
  }
}

/** Why two versions could not be compared, in the page's words (the server's category is never shown). */
export function compareFailureText(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return 'One of these versions is no longer there.'
  }
  if (error instanceof ApiError && error.status === 500) {
    return 'One of these versions can no longer be read, so the two cannot be compared.'
  }
  return 'The two versions could not be compared. Try again.'
}

/** What asked for a version, in the page's words. */
export function askedFor(entry: TimelineEntry): string {
  if (entry.origin === 'duplicate') {
    const from = entry.duplicatedFrom
    const title = from?.classTitle ?? null
    const version = from?.number !== null && from?.number !== undefined ? `, version ${String(from.number)}` : ''
    return title !== null ? `Duplicated from “${title}”${version}` : 'Duplicated from a class that is gone'
  }
  if (entry.origin === 'revise_draft') {
    return entry.request !== null ? `You asked: “${entry.request}”` : 'Changed on request'
  }
  if (entry.origin === 'revise_songs') return 'Songs changed'
  return 'First design'
}

function plural(n: number, one: string, many: string): string {
  return `${String(n)} ${n === 1 ? one : many}`
}

function tssText(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

/** The comparison in plain sentences, from the older version to the newer one. */
export function describeComparison(c: Comparison): string[] {
  const lines: string[] = []
  if (c.segmentsAdded > 0 || c.segmentsRemoved > 0) {
    lines.push(
      `Segments: ${String(c.segmentsAdded)} added, ${String(c.segmentsRemoved)} removed.`,
    )
  }
  const touched = c.intervalsAdded + c.intervalsRemoved + c.intervalsChanged
  if (touched > 0) {
    const parts: string[] = []
    if (c.intervalsChanged > 0) parts.push(`${plural(c.intervalsChanged, 'interval', 'intervals')} changed`)
    if (c.intervalsAdded > 0) parts.push(`${plural(c.intervalsAdded, 'interval', 'intervals')} added`)
    if (c.intervalsRemoved > 0) parts.push(`${plural(c.intervalsRemoved, 'interval', 'intervals')} removed`)
    lines.push(`${parts.join(', ')}.`)
  }
  if (c.secondsBefore !== c.secondsAfter) {
    const diff = Math.abs(c.secondsAfter - c.secondsBefore)
    const word = c.secondsAfter > c.secondsBefore ? 'longer' : 'shorter'
    lines.push(`Length: ${clock(c.secondsBefore)} to ${clock(c.secondsAfter)} (${clock(diff)} ${word}).`)
  } else {
    lines.push(`Length: the same, ${clock(c.secondsAfter)}.`)
  }
  lines.push(
    c.tssBefore === c.tssAfter
      ? `TSS: the same, ${tssText(c.tssAfter)}.`
      : `TSS: ${tssText(c.tssBefore)} to ${tssText(c.tssAfter)}.`,
  )
  if (c.unknownBpmBefore !== c.unknownBpmAfter || c.unsureBefore !== c.unsureAfter) {
    lines.push(
      `Timing warnings: ${String(c.unknownBpmBefore + c.unsureBefore)} to ${String(c.unknownBpmAfter + c.unsureAfter)}.`,
    )
  }
  lines.push(
    c.sameSource && c.sameFingerprint
      ? 'Music: the same.'
      : c.sameSource
        ? 'Music: the same source, but its songs changed.'
        : 'Music: different music.',
  )
  if (touched === 0 && c.segmentsAdded === 0 && c.segmentsRemoved === 0) {
    lines.unshift(c.textChanged ? 'Only names, descriptions or cues differ.' : 'The workouts are the same.')
  } else if (c.textChanged) {
    lines.push('Some names, descriptions or cues differ too.')
  }
  return lines
}
