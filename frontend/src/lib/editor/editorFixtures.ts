import type { EditRecord } from '../../api/classEdits'
import type { Timeline, TimelineTrack } from '../../api/studioOutputs'
import { decodeBlocks, type EditorBlock } from './blocks'

// Test fixtures for the editor's pure modules and pages.

const interval = (sec: number, zone: number, extra: Record<string, unknown> = {}) => ({ sec, zone, rpm: [80, 90], standing: false, cue: '', ...extra })

/** warmup 60 s; main 120 s, 90 s (FTP test, 20 min is not used: ftp_test marks it), 60 s; cooldown 30 s. */
export const CONTENT = {
  name: 'Test class',
  description: '',
  segments: [
    { name: 'Warm up', kind: 'warmup', intervals: [interval(60, 2)] },
    { name: 'Main', kind: 'main', intervals: [interval(120, 4), interval(90, 5, { ftp_test: 3 }), interval(60, 3, { game: 'racetrack' })] },
    { name: 'Cool down', kind: 'cooldown', intervals: [interval(30, 1)] },
  ],
}

export function blocksOf(content: unknown = CONTENT): EditorBlock[] {
  const blocks = decodeBlocks(content)
  if (blocks === null) throw new Error('fixture content must decode')
  return blocks
}

export function record(extra: Partial<EditRecord>): EditRecord {
  return { type: 'lock', segmentKind: null, span: null, field: null, scope: null, edge: null, piece: null, value: null, aiValue: null, confirmed: null, ...extra }
}

const track = (position: number, title: string, mixStart: number, mixEnd: number, extra: Partial<TimelineTrack> = {}): TimelineTrack => ({
  position,
  title,
  artist: 'Artist',
  bpm: 120,
  startSec: 10,
  endSec: 10 + (mixEnd - mixStart),
  playedSec: mixEnd - mixStart,
  effectiveSec: mixEnd - mixStart,
  mixStartSec: mixStart,
  mixEndSec: mixEnd,
  crossfadeInSec: position === 0 ? 0 : 8,
  crossfadeOutSec: position === 1 ? 0 : 8,
  repeatOf: null,
  loop: false,
  firstDownbeatSec: mixStart + 1,
  ...extra,
})

export function timelineOf(extra: Partial<Timeline> = {}): Timeline {
  return {
    revisionId: 'rev-1',
    contentHash: 'a'.repeat(64),
    durationSec: 360,
    workoutSec: 360,
    tracks: [track(0, 'First song', 0, 188), track(1, 'Second song', 180, 360)],
    hasRepeats: false,
    hints: [],
    crossfades: [{ after: 0, sec: 8, mixFromSec: 180, mixToSec: 188 }],
    intervals: [],
    specDigest: 'b'.repeat(64),
    proposalRevisionId: 'prop-1',
    isPlain: false,
    choices: null,
    ...extra,
  }
}
