import { describe, expect, it } from 'vitest'
import type { ClassSummary } from '../../api/classes'
import { NO_COMPOSER_STATE, type ComposerState } from '../../api/composerState'
import { deriveChip, type ChipInput } from './chip'

const SKETCH_ID = 'r-sketch'
const NO_SONGS = { state: 'none', jobId: null, revisionId: null, contentHash: null, expiresAt: null }
const SAVED_NONE: ClassSummary['saved'] = {
  count: 0,
  latestRevisionNumber: null,
  latestSavedAt: null,
  latestUpToDate: null,
  latestSavedWorkoutId: null,
  detachedCount: 0,
  latestVerified: null,
}

function input(over: Omit<Partial<ChipInput>, 'composer'> & { composer?: Partial<ComposerState> } = {}): ChipInput {
  const { composer, ...rest } = over
  return {
    currentRevisionId: SKETCH_ID,
    currentOrigin: 'sketch',
    openJob: null,
    saved: SAVED_NONE,
    songs: NO_SONGS,
    ...rest,
    composer: { ...NO_COMPOSER_STATE, ...composer },
  }
}

const FILL = { sketchRevisionId: SKETCH_ID, attempt: 1, failure: null }
const running = (kind: string) => ({ id: 'j', kind, state: 'running' })

describe('the workspace state chip', () => {
  const rows: [string, ChipInput, string, boolean, boolean][] = [
    ['a sketch with no fill started', input(), 'rough', false, false],
    ['a sketch while the fill runs', input({ openJob: running('design_with_songs'), composer: { fill: FILL } }), 'rough', false, false],
    ['a list waits for approval', input({ songs: { ...NO_SONGS, state: 'waiting' }, composer: { fill: FILL } }), 'ready', false, false],
    ['a list waits while a swap job is queued', input({ songs: { ...NO_SONGS, state: 'waiting' }, openJob: { id: 'j', kind: 'design_with_songs', state: 'queued' }, composer: { fill: FILL } }), 'rough', false, false],
    ['the list expired for this sketch', input({ songs: { ...NO_SONGS, state: 'expired' }, composer: { fill: FILL } }), 'expired', true, false],
    ['the fill failed for this sketch', input({ composer: { fill: { ...FILL, failure: 'invalid_output' } } }), 'rough', true, false],
    ['a swap failed while running (list cancelled)', input({ songs: { ...NO_SONGS, state: 'cancelled' }, composer: { fill: FILL } }), 'rough', true, false],
    ['an expired list of an older sketch', input({ songs: { ...NO_SONGS, state: 'expired' }, composer: { fill: { ...FILL, sketchRevisionId: 'r-old' } } }), 'rough', false, false],
    ['phase 2 failed after the approval', input({ songs: { ...NO_SONGS, state: 'approved' }, composer: { fill: FILL, phase2: 'failed' } }), 'playlistNotCreated', false, true],
    ['phase 2 failed, then the sketch was revised', input({ songs: { ...NO_SONGS, state: 'approved' }, composer: { fill: { ...FILL, sketchRevisionId: 'r-old' }, phase2: 'failed' } }), 'rough', false, false],
    ['phase 2 runs after the approval', input({ songs: { ...NO_SONGS, state: 'approved' }, openJob: running('design_with_songs'), composer: { fill: FILL } }), 'rough', false, false],
    ['save_pending is set', input({ currentOrigin: 'design_with_songs', composer: { savePending: { revisionId: 'd', contentHash: 'h', jobId: 'p' } } }), 'saving', false, false],
    ['the save runs', input({ currentOrigin: 'design_with_songs', openJob: running('save_workout') }), 'saving', false, false],
    ['a draft saved and up to date', input({ currentOrigin: 'design_with_songs', saved: { ...SAVED_NONE, count: 1, latestUpToDate: true } }), 'saved', false, false],
    ['a draft not saved', input({ currentOrigin: 'design_with_songs' }), 'draft', false, false],
    ['a draft changed since the save', input({ currentOrigin: 'revise_draft', saved: { ...SAVED_NONE, count: 1, latestUpToDate: false } }), 'draft', false, false],
  ]
  it.each(rows)('%s', (_name, given, state, findAgain, createAgain) => {
    expect(deriveChip(given)).toEqual({ state, findAgain, createAgain })
  })
})
