import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { hasCursor } from '../../preview/transport'
import { useSilentCursor } from './useSilentCursor'

let clock = 1000

beforeEach(() => {
  clock = 1000
  vi.useFakeTimers()
  vi.spyOn(performance, 'now').mockImplementation(() => clock)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

const tick = async (ms: number) => {
  clock += ms
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('the silent cursor hook: the cursor is what the transport observed', () => {
  it('has no cursor before anything is auditioned', () => {
    const { result } = renderHook(() => useSilentCursor(60_000))
    expect(result.current.state.status).toBe('idle')
    expect(hasCursor(result.current.state)).toBe(false)
  })

  it('draws the clock, not the position asked for: it moves on by itself while playing', async () => {
    const { result } = renderHook(() => useSilentCursor(60_000))
    await act(async () => {
      result.current.audition(0, 0, 10_000)
      await Promise.resolve()
    })
    expect(result.current.state).toMatchObject({ status: 'playing', transportMs: 10_000 })
    await tick(2000)
    expect(result.current.state.transportMs).toBe(12_000)
    await act(async () => {
      result.current.pause()
      await Promise.resolve()
    })
    expect(result.current.state).toMatchObject({ status: 'paused', transportMs: 12_000 })
    await tick(5000)
    expect(result.current.state.transportMs).toBe(12_000) // paused: the clock does not move it
  })

  it('shows no cursor for a position outside the class, and goes idle when the clock runs out', async () => {
    const { result } = renderHook(() => useSilentCursor(5000))
    await act(async () => {
      result.current.audition(0, 0, 9000)
      await Promise.resolve()
    })
    expect(hasCursor(result.current.state)).toBe(false)
    await act(async () => {
      result.current.audition(0, 0, 4000)
      await Promise.resolve()
    })
    expect(hasCursor(result.current.state)).toBe(true)
    await tick(2000)
    expect(result.current.state.status).toBe('idle')
    expect(hasCursor(result.current.state)).toBe(false)
  })

  it('starts again when the class length changes', async () => {
    const { result, rerender } = renderHook(({ total }) => useSilentCursor(total), { initialProps: { total: 60_000 } })
    await act(async () => {
      result.current.audition(0, 0, 10_000)
      await Promise.resolve()
    })
    expect(hasCursor(result.current.state)).toBe(true)
    rerender({ total: 90_000 })
    expect(hasCursor(result.current.state)).toBe(false)
  })
})
