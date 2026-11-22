import { describe, expect, it } from 'vitest'
import { decodePreview, recordedChoices } from '../../api/studioMix'
import { previewBody, suggestion } from '../../test/mixFixtures'
import {
  DEFAULT_MIX,
  backToSuggestion,
  changeSongsOffered,
  choiceSummary,
  fromRecorded,
  hardCut,
  keepWhole,
  replanOffered,
  setPositions,
  stepOf,
  toBody,
} from './mixChoices'

const SUGGESTIONS = decodePreview(previewBody()).suggestions

describe('the choices request', () => {
  it('is the whole-songs form, with nothing else, when DJ mix is off', () => {
    const state = keepWhole(DEFAULT_MIX, [1])
    expect(toBody(false, state)).toEqual({ dj_mix: false, blend: false, trim: false, keep_whole: [], hard_cut_in: [], hard_cut_out: [] })
  })

  it('carries the switches and the sorted sets under DJ mix', () => {
    const state = setPositions(setPositions(DEFAULT_MIX, 'keepWhole', [3, 1, 3], true), 'hardIn', [2], true)
    expect(toBody(true, state)).toEqual({
      dj_mix: true, blend: true, trim: true, keep_whole: [1, 3], hard_cut_in: [2], hard_cut_out: [],
    })
  })

  it('does not send positions the user cannot see because their switch is off', () => {
    const state = hardCut(keepWhole(DEFAULT_MIX, [1, 3]), [2])
    expect(toBody(true, { ...state, blend: false })).toEqual({
      dj_mix: true, blend: false, trim: true, keep_whole: [1, 3], hard_cut_in: [], hard_cut_out: [],
    })
    expect(toBody(true, { ...state, trim: false })).toEqual({
      dj_mix: true, blend: true, trim: false, keep_whole: [], hard_cut_in: [2], hard_cut_out: [2],
    })
    // Switching back on brings the user's earlier choices back: the local state keeps them.
    expect(toBody(true, state)).toMatchObject({ keep_whole: [1, 3], hard_cut_in: [2] })
  })

  it('does bulk actions on a selection and takes them back', () => {
    const cut = hardCut(keepWhole(DEFAULT_MIX, [0, 1]), [1, 2])
    expect(cut).toMatchObject({ keepWhole: [0, 1], hardIn: [1, 2], hardOut: [1, 2] })
    expect(backToSuggestion(cut, [1])).toMatchObject({ keepWhole: [0], hardIn: [2], hardOut: [2] })
  })

  it('pre-fills from recorded choices and ignores an unreadable record', () => {
    const body = recordedChoices({ dj_mix: true, blend: false, trim: true, keep_whole: [2], hard_cut_in: [] })
    expect(body).not.toBeNull()
    expect(fromRecorded(body)).toMatchObject({ blend: false, trim: true, keepWhole: [2], hardOut: [] })
    expect(recordedChoices({ dj_mix: true, blend: true, trim: 'yes' })).toBeNull()
    expect(recordedChoices({ dj_mix: true, blend: true, trim: true, keep_whole: [-1] })).toBeNull()
    expect(fromRecorded(recordedChoices({ dj_mix: false, blend: false, trim: false }))).toEqual(DEFAULT_MIX)
  })
})

describe('what the preview means', () => {
  const ready = (over: Record<string, unknown>) => ({ status: 'ready' as const, preview: decodePreview(previewBody(over)) })
  const digest = '5'.repeat(64)

  it('is Approve only when the digests are equal', () => {
    expect(stepOf(ready({}), digest)).toBe('ready')
    expect(stepOf(ready({ spec_digest: '6'.repeat(64) }), digest)).toBe('apply')
    expect(stepOf({ status: 'loading' }, digest)).toBe('checking')
  })

  it('never offers Approve for a refusal, whatever the digest', () => {
    expect(stepOf(ready({ refusal: { reason: 'window_too_short' } }), digest)).toBe('refused')
    expect(stepOf(ready({ spec_digest: null, refusal: { reason: 'no_proposal' } }), digest)).toBe('no_proposal')
    expect(stepOf({ status: 'failed', reason: 'no_proposal', message: '' }, digest)).toBe('no_proposal')
    expect(stepOf({ status: 'failed', reason: 'stale_base', message: '' }, digest)).toBe('unavailable')
  })

  it('offers the re-plan for a length reason only while something can still be trimmed', () => {
    const all = (n: number) => Array.from({ length: n }, (_, i) => i)
    expect(replanOffered('class_length_out_of_tolerance', true, DEFAULT_MIX, SUGGESTIONS)).toBe(true)
    expect(replanOffered('class_length_out_of_tolerance', false, DEFAULT_MIX, SUGGESTIONS)).toBe(false)
    expect(replanOffered('plan_class_too_long', true, { ...DEFAULT_MIX, trim: false }, SUGGESTIONS)).toBe(false)
    expect(replanOffered('plan_class_too_long', true, keepWhole(DEFAULT_MIX, all(3)), SUGGESTIONS)).toBe(false)
    const onlyFull = [suggestion(0, { full_only: 'timing_unusable' })].map((s) => decodePreview(previewBody({ suggestions: [s] })).suggestions[0]!)
    expect(replanOffered('plan_class_too_long', true, DEFAULT_MIX, onlyFull)).toBe(false)
    expect(changeSongsOffered('plan_class_too_long', false)).toBe(true)
    expect(changeSongsOffered('plan_class_too_long', true)).toBe(false)
  })

  it('always offers the re-plan for the reasons that need a re-design, and for nothing else', () => {
    for (const why of ['workout_does_not_fit', 'plan_not_allowed', 'no_proposal']) {
      expect(replanOffered(why, false, DEFAULT_MIX, [])).toBe(true)
      expect(changeSongsOffered(why, true)).toBe(false)
    }
    expect(replanOffered('window_too_short', true, DEFAULT_MIX, SUGGESTIONS)).toBe(false)
    expect(changeSongsOffered('window_too_short', false)).toBe(false)
  })
})

describe('the summary of a draft', () => {
  it('counts the songs shorter than the song and the changes with no crossfade', () => {
    const windows = [
      { position: 0, startSec: 0, endSec: 190 },
      { position: 1, startSec: 0, endSec: 244.8 },
      { position: 2, startSec: 12, endSec: 245 },
      { position: 2, startSec: 0, endSec: 100 },
    ]
    expect(choiceSummary(windows, SUGGESTIONS, [{ sec: 8 }, { sec: 0 }, { sec: 0 }])).toEqual({ trimmed: 2, hardCuts: 2 })
  })
})
