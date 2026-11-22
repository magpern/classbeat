import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WINDOW_MS, flush, rig, type Rig } from '../test/fakeClips'

// ADR 0042 section 6.3 and 6.4: what ends a session, what keeps it, and what the transport says when
// the server cannot give a clip.

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

const PLAY = { piece: 0, sourceMs: 0 }

async function play(r: Rig, classMs = 0) {
  const ack = await r.transport.audition({ ...PLAY, classMs })
  if (ack.ok) r.current().fire('playing')
  return ack
}

describe('410 preview_session_ended', () => {
  it('on a clip request ends the session; the next Play starts a new one, never the old id', async () => {
    const r = rig()
    await play(r)
    await r.advance(20_000)
    r.service.failures.set(500_000, 'session_ended')
    const ack = await r.transport.audition({ ...PLAY, classMs: 500_000 })
    expect(ack).toEqual({ ok: false, reason: 'session_ended' })
    expect(r.transport.observe()).toMatchObject({ status: 'paused', reason: 'session_ended' })
    expect(r.audio.every((a) => a.paused)).toBe(true)
    await r.advance(30_000)
    expect(r.transport.listenedMs()).toBe(20_000) // nothing runs after it
    r.service.failures.clear()
    expect((await play(r, 500_000)).ok).toBe(true)
    expect(r.service.sessions).toEqual(['s1', 's2'])
    expect(r.service.requests.at(-1)).toEqual({ sessionId: 's2', startMs: 500_000 })
    expect(r.transport.listenedMs()).toBe(0)
  })

  it('on the preload of the next window stops playback at once and says so', async () => {
    const r = rig()
    r.service.failures.set(WINDOW_MS, 'session_ended')
    await play(r)
    r.current().currentTime = 200
    r.current().fire('timeupdate')
    await flush()
    expect(r.transport.observe()).toMatchObject({ status: 'paused', reason: 'session_ended' })
    expect(r.audio.every((a) => a.paused)).toBe(true)
  })
})

describe('the audition is off or the session is not the user’s (404)', () => {
  it('says unavailable when the session cannot be made, and makes no cursor', async () => {
    const r = rig()
    r.service.createFailure = 'unavailable'
    const ack = await r.transport.audition({ ...PLAY, classMs: 5000 })
    expect(ack).toEqual({ ok: false, reason: 'unavailable' })
    expect(r.transport.observe()).toMatchObject({ status: 'error', reason: 'unavailable' })
    expect(r.service.requests).toHaveLength(0)
  })

  it('on a clip request ends the session as unavailable', async () => {
    const r = rig()
    await play(r)
    r.service.failures.set(300_000, 'unavailable')
    await r.transport.audition({ ...PLAY, classMs: 300_000 })
    expect(r.transport.observe()).toMatchObject({ status: 'error', reason: 'unavailable' })
  })
})

describe('a clip that cannot be had: the session stays, and so does the counter, until the user retries', () => {
  it('429 on a seek stops playback with a reason; the retry is the same session', async () => {
    const r = rig()
    await play(r)
    await r.advance(10_000)
    r.service.failures.set(700_000, 'rate_limited')
    await r.transport.audition({ ...PLAY, classMs: 700_000 })
    expect(r.transport.observe()).toMatchObject({ status: 'error', reason: 'rate_limited', transportMs: 700_000 })
    r.service.failures.clear()
    await play(r, 700_000) // Retry
    expect(r.service.sessions).toHaveLength(1)
    await r.advance(5000)
    expect(r.transport.listenedMs()).toBe(15_000)
    expect(r.transport.observe().status).toBe('playing')
  })

  it('a failed preload does not stop what plays; at the window end the failure is said, and a retry asks again', async () => {
    const r = rig()
    r.service.failures.set(WINDOW_MS, 'render_failed')
    await play(r)
    r.current().currentTime = 200
    r.current().fire('timeupdate')
    await flush()
    expect(r.transport.observe().status).toBe('playing')
    await r.advance(40_000)
    const first = r.current()
    first.fire('ended')
    first.paused = true
    expect(r.transport.observe()).toMatchObject({ status: 'error', reason: 'render_failed', transportMs: WINDOW_MS })
    r.service.failures.clear()
    await play(r, WINDOW_MS)
    expect(r.service.sessions).toHaveLength(1)
    expect(r.service.requests.at(-1)).toEqual({ sessionId: 's1', startMs: WINDOW_MS })
  })

  it('says the window is rendering while it is, and playing once it plays', async () => {
    const r = rig()
    r.service.rendering.add(0)
    r.service.hold.add(0)
    const started = r.transport.audition({ ...PLAY, classMs: 0 })
    await flush()
    expect(r.transport.observe()).toMatchObject({ status: 'starting', reason: 'rendering' })
    r.service.held[0]?.release({ url: '/clip/s1/0', startMs: 0, endMs: WINDOW_MS })
    await started
    r.current().fire('playing')
    expect(r.transport.observe()).toMatchObject({ status: 'playing' })
    expect(r.transport.observe().reason).toBeUndefined()
  })

  it('a blocked play() is said in words, not thrown', async () => {
    const r = rig()
    r.audio[0].playRejects = new DOMException('blocked', 'NotAllowedError')
    r.audio[1].playRejects = new DOMException('blocked', 'NotAllowedError')
    await r.transport.audition({ ...PLAY, classMs: 0 })
    await flush()
    expect(r.transport.observe()).toMatchObject({ status: 'error', reason: 'blocked' })
  })

  it('a draft that can no longer be previewed (409) is said', async () => {
    const r = rig()
    r.service.createFailure = 'draft_changed'
    await r.transport.audition({ ...PLAY, classMs: 0 })
    expect(r.transport.observe()).toMatchObject({ status: 'error', reason: 'draft_changed' })
  })
})

describe('an edit during playback', () => {
  it('an audio edit fetches fresh clips for the playing position, in the same session, with the counter kept', async () => {
    const r = rig()
    await play(r)
    await r.advance(30_000)
    r.current().currentTime = 30
    const before = r.current()
    r.transport.invalidate()
    await flush()
    expect(r.service.requests.slice(-1)).toEqual([{ sessionId: 's1', startMs: 30_000 }]) // fresh, from where it is
    expect(r.service.sessions).toHaveLength(1)
    const after = r.current()
    expect(after).not.toBe(before) // the fresh clip plays in the other element
    expect(r.transport.listenedMs()).toBe(30_000)
    after.fire('playing')
    await r.advance(5000)
    expect(r.transport.listenedMs()).toBe(35_000)
  })

  it('does not seek inside a stale clip, and drops the preloaded next window', async () => {
    const r = rig()
    await play(r)
    r.current().currentTime = 200
    r.current().fire('timeupdate')
    await flush()
    expect(r.service.requests.map((x) => x.startMs)).toEqual([0, WINDOW_MS])
    r.transport.invalidate()
    await flush()
    await r.transport.audition({ ...PLAY, classMs: 100_000 })
    expect(r.service.requests.at(-1)?.startMs).toBe(100_000) // a new clip, not the stale window
  })

  it('while paused only forgets: nothing is requested and no session starts', async () => {
    const r = rig()
    await play(r)
    await r.transport.pause()
    const asked = r.service.requests.length
    r.transport.invalidate()
    await flush()
    expect(r.service.requests).toHaveLength(asked)
    expect(r.service.sessions).toHaveLength(1)
  })
})

describe('two Plays at once', () => {
  it('make one session, and the later request wins', async () => {
    const r = rig()
    r.service.hold.add(10_000)
    const first = r.transport.audition({ ...PLAY, classMs: 10_000 })
    const second = r.transport.audition({ ...PLAY, classMs: 50_000 })
    await flush()
    r.service.held[0]?.release({ url: '/clip/s1/10000', startMs: 10_000, endMs: 10_000 + WINDOW_MS })
    expect(await first).toEqual({ ok: false, reason: 'superseded' })
    expect(await second).toEqual({ ok: true })
    expect(r.service.sessions).toHaveLength(1)
    expect(r.transport.observe().transportMs).toBe(50_000)
  })

  it('a Pause before the session exists leaves none playing', async () => {
    const r = rig()
    const started = r.transport.audition({ ...PLAY, classMs: 0 })
    await r.transport.pause()
    expect(await started).toEqual({ ok: false, reason: 'superseded' })
    expect(r.audio.every((a) => a.paused)).toBe(true)
    expect(r.transport.observe().status).toBe('paused')
    await r.advance(20_000)
    expect(r.transport.listenedMs()).toBe(0)
  })
})
