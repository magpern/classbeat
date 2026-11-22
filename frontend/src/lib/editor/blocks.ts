import { isObject } from '../../api/decode'

// The editable unit of the Class Preview & Editor: the block, one interval of the stored draft,
// addressed by its flat index in class order (backend `workout_edit_policy.blocks`). The index
// must match the server's, so blocks are read from the revision's content exactly as stored.
// Pure: no React, no fetch.

export type Rpm = [number, number] | 'beat'

export interface EditorBlock {
  /** The flat index in class order: what every edit names. */
  index: number
  segment: number
  segmentName: string
  segmentKind: string
  startMs: number
  endMs: number
  sec: number
  zone: number
  rpm: Rpm
  standing: boolean
  cue: string
  game: string | null
  /** An FTP test block is not dragged, split or merged by the server. */
  ftpTest: boolean
}

const MAX_SEC = 3600
/** The most a game block and the block after it can shrink to (`mowl.workout.MIN_GAME_SEC`). */
export const GAME_MIN_SEC = 10

function rpmOf(value: unknown): Rpm | null {
  if (value === 'beat') return 'beat'
  if (Array.isArray(value) && value.length === 2 && value.every((n) => typeof n === 'number')) return [value[0] as number, value[1] as number]
  return null
}

/**
 * The blocks of a stored draft's content, or null when it has a shape the editor does not edit
 * (a running draft has no cadence; a malformed one is not guessed at).
 */
export function decodeBlocks(content: unknown): EditorBlock[] | null {
  if (!isObject(content) || !Array.isArray(content['segments'])) return null
  const out: EditorBlock[] = []
  let at = 0
  for (const [s, segment] of (content['segments'] as unknown[]).entries()) {
    if (!isObject(segment) || !Array.isArray(segment['intervals'])) return null
    for (const interval of segment['intervals'] as unknown[]) {
      if (!isObject(interval)) return null
      const rpm = rpmOf(interval['rpm'])
      const { sec, zone } = interval
      if (rpm === null || typeof sec !== 'number' || !Number.isInteger(sec) || sec < 1 || sec > MAX_SEC || typeof zone !== 'number') return null
      out.push({
        index: out.length,
        segment: s,
        segmentName: typeof segment['name'] === 'string' ? segment['name'] : '',
        segmentKind: typeof segment['kind'] === 'string' ? segment['kind'] : '',
        startMs: at,
        endMs: at + sec * 1000,
        sec,
        zone,
        rpm,
        standing: interval['standing'] === true,
        cue: typeof interval['cue'] === 'string' ? interval['cue'] : '',
        game: typeof interval['game'] === 'string' ? interval['game'] : null,
        ftpTest: typeof interval['ftp_test'] === 'number',
      })
      at += sec * 1000
    }
  }
  return out.length === 0 ? null : out
}

export function totalSec(blocks: readonly EditorBlock[]): number {
  return blocks.reduce((sum, b) => sum + b.sec, 0)
}

/** A block's minimum length: 1 s, 10 s for a game and the block after one. */
export function minSec(blocks: readonly EditorBlock[], index: number): number {
  const game = blocks[index]?.game !== null || (index > 0 && blocks[index - 1]?.game !== null && blocks[index - 1]?.game !== undefined)
  return game ? GAME_MIN_SEC : 1
}
