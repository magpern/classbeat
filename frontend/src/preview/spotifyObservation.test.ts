import { describe, expect, it } from 'vitest'
import type { PlayerState } from '../api/spotifyPreview'
import { judge, observationOf, pollDelayMs, positionAt, SEEK_MS, uncertaintyAt, type Observation } from './spotifyObservation'

const state = (extra: Partial<PlayerState> = {}): PlayerState => ({
  status: 'playing',
  pieces: [0],
  progressMs: 10_000,
  timestamp: 1000,
  device: { name: 'Kitchen', type: 'Speaker' },
  ...extra,
})
const seen = (seq: number, sentAt: number, receivedAt: number, extra: Partial<PlayerState> = {}, epoch = 1): Observation =>
  observationOf(seq, epoch, sentAt, receivedAt, state(extra))

describe('observationOf', () => {
  it('reads the player at the middle of the round trip', () => {
    const o = seen(1, 1000, 1600)
    expect(o.observedAt).toBe(1300)
    expect(o.halfRoundTripMs).toBe(300)
  })

  it('never has a negative round trip', () => {
    expect(seen(1, 1000, 900)).toMatchObject({ observedAt: 1000, halfRoundTripMs: 0 })
  })
})

describe('position and uncertainty', () => {
  it('moves on while it plays and holds while it is paused', () => {
    expect(positionAt(seen(1, 0, 200), 1100)).toBe(11_000)
    expect(positionAt(seen(1, 0, 200, { status: 'paused' }), 9000)).toBe(10_000)
  })

  it('has no position when the player gave none', () => {
    expect(positionAt(seen(1, 0, 200, { progressMs: null }), 500)).toBeNull()
  })

  it('is half the round trip plus the age of the poll; a paused player adds no age', () => {
    expect(uncertaintyAt(seen(1, 0, 1000), 1000)).toBe(500)
    expect(uncertaintyAt(seen(1, 0, 1000), 1250)).toBe(750)
    expect(uncertaintyAt(seen(1, 0, 1000, { status: 'paused' }), 9000)).toBe(500)
  })
})

describe('judge', () => {
  const held = seen(5, 1000, 1200)

  it('takes the first observation of an epoch', () => {
    expect(judge(null, seen(1, 0, 100), 1)).toEqual({ accepted: true, notes: [] })
  })

  it('refuses an answer from another epoch: it was asked before a command', () => {
    expect(judge(held, seen(6, 1000, 1200, {}, 0), 1)).toEqual({ accepted: false, why: 'older_epoch' })
    expect(judge(null, seen(1, 0, 100, {}, 0), 1)).toEqual({ accepted: false, why: 'older_epoch' })
  })

  it('refuses an answer to an earlier ask, however late it arrived', () => {
    expect(judge(held, seen(4, 0, 9000, { timestamp: null }), 1)).toEqual({ accepted: false, why: 'older_seq' })
    expect(judge(held, seen(5, 0, 9000), 1)).toEqual({ accepted: false, why: 'older_seq' })
  })

  it('refuses a player state older than the one held, even for a later ask', () => {
    expect(judge(held, seen(6, 2000, 2200, { timestamp: 900 }), 1)).toEqual({ accepted: false, why: 'older_timestamp' })
    expect(judge(held, seen(6, 2000, 2200, { timestamp: 1000 }), 1)).toMatchObject({ accepted: true })
    expect(judge(held, seen(6, 2000, 2200, { timestamp: null }), 1)).toMatchObject({ accepted: true })
  })

  it('notes a seek only beyond the threshold', () => {
    const at = (progressMs: number) => judge(held, seen(6, 2000, 2200, { progressMs, timestamp: 2000 }), 1)
    // expected: 10_000 + (2100 - 1100) = 11_000
    expect(at(11_000 + SEEK_MS)).toEqual({ accepted: true, notes: [] })
    expect(at(11_000 + SEEK_MS + 1)).toEqual({ accepted: true, notes: ['seek_detected'] })
    expect(at(11_000 - SEEK_MS - 1)).toEqual({ accepted: true, notes: ['seek_detected'] })
  })

  it('does not call a change of track a seek', () => {
    const next = seen(6, 2000, 2200, { pieces: [1], progressMs: 90_000, timestamp: 2000 })
    expect(judge(held, next, 1)).toEqual({ accepted: true, notes: [] })
  })

  it('notes a device switch', () => {
    const next = seen(6, 2000, 2200, { device: { name: 'Phone', type: 'Smartphone' }, progressMs: 11_000, timestamp: 2000 })
    expect(judge(held, next, 1)).toEqual({ accepted: true, notes: ['device_switched'] })
  })
})

describe('pollDelayMs', () => {
  it('polls about every second, only when wanted on a visible page', () => {
    expect(pollDelayMs(true, true, 0, null)).toBe(1000)
    expect(pollDelayMs(false, true, 0, null)).toBeNull()
    expect(pollDelayMs(true, false, 0, null)).toBeNull()
  })

  it('waits out a rate limit', () => {
    expect(pollDelayMs(true, true, 1000, 8000)).toBe(7000)
    expect(pollDelayMs(true, true, 9000, 8000)).toBe(1000)
  })
})
