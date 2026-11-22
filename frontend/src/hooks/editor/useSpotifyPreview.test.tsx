import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CLASS_PATH } from '../../test/classFixtures'
import { fakeApi, json } from '../../test/fakeApi'
import { useSpotifyPreview } from './useSpotifyPreview'

// The hook's own guarantees, below the page: one correction write at a time, each on the draft the
// last answer named, and a write that throws ends as a sentence, never a stuck "saving".

/** Runs `fn` inside act and lets the promises it started settle. */
const flushed = (fn: () => void) => act(() => { fn(); return Promise.resolve() })

const hooks = vi.hoisted(() => ({ throwOnWrite: false }))
vi.mock('../../api/spotifyPreview', async (original) => {
  const real = await original<typeof import('../../api/spotifyPreview')>()
  return {
    ...real,
    setCorrection: (...args: Parameters<typeof real.setCorrection>) => {
      if (hooks.throwOnWrite) throw new Error('the write blew up')
      return real.setCorrection(...args)
    },
  }
})

const A = `${CLASS_PATH}/preview/spotify`
const song = (piece: number) => ({
  piece,
  song: piece + 1,
  title: `Song ${String(piece + 1)}`,
  artist: 'x',
  length_ms: 200_000,
  analysis: { spotdown_job: 'j', sha256: 'ab'.repeat(32), timing_class: 'good' },
  alignment: { offset_ms: 0, source: 'spotdown' },
  correction: null,
})
const alignment = (revision: string) => ({ revision_id: revision, map_version: 1, crossfade_ms: 10_000, songs: [song(0), song(1)] })
const CLASS_ID = CLASS_PATH.split('/').pop() ?? ''

beforeEach(() => {
  hooks.throwOnWrite = false
})

async function ready(routes: Record<string, unknown>) {
  const api = fakeApi({ [`GET ${A}/alignment`]: alignment('r1'), ...routes })
  const hook = renderHook(() => useSpotifyPreview(CLASS_ID))
  await waitFor(() => { expect(hook.result.current.load.status).toBe('ready') })
  return { api, hook }
}

describe('correct', () => {
  it('sends one write at a time even when called twice in the same tick', async () => {
    let answer: (value: unknown) => void = () => undefined
    const { api, hook } = await ready({ [`PUT ${A}/corrections/1`]: () => new Promise((resolve) => { answer = resolve }) })
    act(() => {
      hook.result.current.correct(1, 100)
      hook.result.current.correct(1, 200)
    })
    await waitFor(() => { expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(1) })
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)))
    expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(1) // the second call was refused, not queued
    await flushed(() => { answer(alignment('r1')) })
    await waitFor(() => { expect(hook.result.current.saving).toBe(false) })
  })

  it('names, in the next write, the draft the last answer named', async () => {
    const { api, hook } = await ready({ [`PUT ${A}/corrections/1`]: alignment('r2') })
    await flushed(() => { hook.result.current.correct(1, 100) })
    await waitFor(() => { expect(hook.result.current.saving).toBe(false) })
    await flushed(() => { hook.result.current.correct(1, 200) })
    await waitFor(() => { expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(2) })
    expect(api.to('PUT', `${A}/corrections/1`).map((c) => (c.body as { revision_id: string }).revision_id)).toEqual(['r1', 'r2'])
  })

  it('a write that throws is a failure the page can say, and the next write is allowed', async () => {
    const { hook } = await ready({ [`PUT ${A}/corrections/1`]: alignment('r1') })
    hooks.throwOnWrite = true
    await flushed(() => { hook.result.current.correct(1, 100) })
    await waitFor(() => { expect(hook.result.current.correctionFailure).toEqual({ reason: 'unknown', status: 0, retryAfterS: null }) })
    expect(hook.result.current.saving).toBe(false)
    hooks.throwOnWrite = false
    await flushed(() => { hook.result.current.correct(1, 100) })
    await waitFor(() => { expect(hook.result.current.correctionFailure).toBeNull() })
  })
})

describe('load', () => {
  it('reads nothing of a player until Play, and has no view before the preview is ready', async () => {
    const api = fakeApi({ [`GET ${A}/alignment`]: () => json({ detail: { reason: 'no_draft' } }, 409) })
    const hook = renderHook(() => useSpotifyPreview(CLASS_ID))
    await waitFor(() => { expect(hook.result.current.load.status).toBe('none') })
    expect(hook.result.current.view).toBeNull()
    expect(api.to('GET', `${A}/state`)).toHaveLength(0)
  })
})
