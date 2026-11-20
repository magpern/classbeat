import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useStudioTimeline } from './useStudioTimeline'

const getTimeline = vi.hoisted(() => vi.fn())
vi.mock('../../api/studioOutputs', () => ({ getTimeline }))

const CLASS = 'c1111111-1111-4111-8111-111111111111'

describe('useStudioTimeline (#1173)', () => {
  beforeEach(() => {
    getTimeline.mockReset()
    getTimeline.mockResolvedValue({ kind: 'music_changed' })
  })

  it('does not ask for the timeline of a class the summary says is not a studio class', () => {
    const { result, rerender } = renderHook((p) => useStudioTimeline(CLASS, p.revision, true, false), {
      initialProps: { revision: 'r1' },
    })
    expect(result.current).toEqual({ status: 'done', kind: 'no_draft' })
    rerender({ revision: 'r2' })
    expect(result.current).toEqual({ status: 'done', kind: 'no_draft' })
    expect(getTimeline).not.toHaveBeenCalled()
  })

  it('answers none, with no request, for a class with no version yet', () => {
    const { result } = renderHook(() => useStudioTimeline(CLASS, null, true, true))
    expect(result.current).toEqual({ status: 'done', kind: 'none' })
    expect(getTimeline).not.toHaveBeenCalled()
  })

  it('asks for the timeline of a studio class and reads it again when the version changes', async () => {
    const { result, rerender } = renderHook((p) => useStudioTimeline(CLASS, p.revision, true, true), {
      initialProps: { revision: 'r1' },
    })
    expect(result.current).toEqual({ status: 'loading' })
    await waitFor(() => { expect(result.current).toEqual({ status: 'done', kind: 'music_changed' }) })
    expect(getTimeline).toHaveBeenCalledTimes(1)
    expect(getTimeline.mock.calls[0]![0]).toBe(CLASS)
    rerender({ revision: 'r2' })
    await waitFor(() => { expect(getTimeline).toHaveBeenCalledTimes(2) })
  })

  it('keeps the last answer while a job is open, for a studio class', async () => {
    const { result, rerender } = renderHook((p) => useStudioTimeline(CLASS, 'r1', p.enabled, true), {
      initialProps: { enabled: true },
    })
    await waitFor(() => { expect(result.current.status).toBe('done') })
    rerender({ enabled: false })
    expect(getTimeline).toHaveBeenCalledTimes(1)
  })

  it('starts asking when the summary turns a class into a studio class', async () => {
    const { rerender } = renderHook((p) => useStudioTimeline(CLASS, 'r1', true, p.studio), {
      initialProps: { studio: false },
    })
    expect(getTimeline).not.toHaveBeenCalled()
    rerender({ studio: true })
    await waitFor(() => { expect(getTimeline).toHaveBeenCalledTimes(1) })
  })
})
