import { count, flag, isObject, list, object, optionalText, text, type JsonObject } from './decode'

// The stored draft as the API answers it (split out of jobs.ts, issue 876): types and decoders.

export type DraftGame = 'racetrack' | 'power_pull' | 'fast_lane' | 'overflow' | 'ghost_target'
const GAMES: readonly string[] = ['racetrack', 'power_pull', 'fast_lane', 'overflow', 'ghost_target']

/** An absent or unknown game id reads as no game: a stored value is never mapped to another game. */
function decodeGame(item: JsonObject): { game?: DraftGame } {
  const game = optionalText(item, 'game')
  return game !== null && GAMES.includes(game) ? { game: game as DraftGame } : {}
}

export interface DraftInterval {
  sec: number
  zone: number
  /** Cycling only; null for a running interval (it has no cadence). */
  rpm: [number, number] | 'beat' | null
  standing: boolean
  /** Running only (0 flat, 1 incline); null for a cycling interval. */
  gradient?: number | null
  cue: string
  /** Cycling only: the MOWL game this interval is (ADR 0029 section 3). `ghost_target` is a legacy value, read-only. */
  game?: DraftGame
}

/** A running draft's intervals carry `gradient` and no cadence (ADR 0018 section 2). */
export function isRunningDraft(segments: DraftSegment[]): boolean {
  return segments.some((s) => s.intervals.some((i) => typeof i.gradient === 'number'))
}

export interface DraftSegment {
  name: string
  kind: string
  intervals: DraftInterval[]
}

export interface DraftSummary {
  number: number
  name: string
  description: string
  segments: DraftSegment[]
  unknownBpm: { index: number; title: string }[]
}

function decodeInterval(value: unknown): DraftInterval {
  const item = object(value, 'interval')
  const rpm = item.rpm
  if (rpm === undefined && item.gradient !== undefined) {
    return {
      sec: count(item, 'sec'),
      zone: count(item, 'zone'),
      rpm: null,
      standing: false,
      gradient: count(item, 'gradient'),
      cue: optionalText(item, 'cue') ?? '',
    }
  }
  let decoded: DraftInterval['rpm']
  if (rpm === 'beat') decoded = 'beat'
  else if (Array.isArray(rpm) && rpm.length === 2 && rpm.every((n) => typeof n === 'number')) {
    decoded = [rpm[0] as number, rpm[1] as number]
  } else throw new Error('Unexpected response from the server (rpm).')
  return {
    sec: count(item, 'sec'),
    zone: count(item, 'zone'),
    rpm: decoded,
    standing: flag(item, 'standing'),
    cue: optionalText(item, 'cue') ?? '',
    ...decodeGame(item),
  }
}

function decodeSegment(value: unknown): DraftSegment {
  const item = object(value, 'segment')
  return {
    name: text(item, 'name'),
    kind: text(item, 'kind'),
    intervals: list(item, 'intervals').map(decodeInterval),
  }
}

export interface DraftContent {
  name: string
  description: string
  segments: DraftSegment[]
}

export function decodeDraftContent(value: unknown): DraftContent {
  const content = object(value, 'content')
  return {
    name: text(content, 'name'),
    description: optionalText(content, 'description') ?? '',
    segments: list(content, 'segments').map(decodeSegment),
  }
}

export function decodeUnknownBpm(source: JsonObject): { index: number; title: string }[] {
  return list(source, 'unknown_bpm').map((entry) => {
    const item: JsonObject = isObject(entry) ? entry : {}
    return { index: count(item, 'index'), title: text(item, 'title') }
  })
}

