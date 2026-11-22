import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WINDOW_MS, flush, rig, type Rig } from '../test/fakeClips'
import { LISTEN_LIMIT_MS } from './listenTimer'
import { hasCursor } from './transport'

// ADR 0042 section 6.3, plan R3: the five counter tests, with a fake media element and a fake
// monotonic clock, then what the transport says about its position.

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

const PLAY = { piece: 0, sourceMs: 0 }

/** Play at `classMs` and the browser's `playing`. */
async function play(r: Rig, classMs = 0): Promise<void> {
  const ack = await r.transport.audition({ ...PLAY, classMs })
  expect(ack).toEqual({ ok: true })
  r.current().fire('playing')
}

/** The browser seeks the playing element: `seeking`, a short wait, then `playing`. */
async function seek(r: Rig, classMs: number): Promise<void> {
  await r.transport.audition({ ...PLAY, classMs })
  r.current().fire('seeking')
  await r.advance(300) // the seek in progress is not listening time
  r.current().fire('playing')
}

/** Progress of the playing element to `sec` of its clip and the `timeupdate` that follows. */
function reach(r: Rig, sec: number): void {
  r.current().currentTime = sec
  r.current().fire('timeupdate')
}

describe('R3 (1): a seek adds only the elapsed playing time, never the jump', () => {
  it('forward and backward while playing', async () => {
    const r = rig()
    await play(r)
    await r.advance(10_000)
    await seek(r, 150_000) // 140 s forward
    await r.advance(5000)
    await seek(r, 20_000) // 130 s back
    await r.advance(5000)
    expect(r.transport.listenedMs()).toBe(20_000)
    expect(r.service.sessions).toEqual(['s1']) // the same session throughout
    expect(r.service.requests).toHaveLength(1) // inside the window: no new clip
  })

  it('a seek into another window keeps the session and the counter', async () => {
    const r = rig()
    await play(r)
    await r.advance(30_000)
    const far = r.transport.audition({ ...PLAY, classMs: 600_000 })
    await flush()
    await far
    r.current().fire('playing')
    await r.advance(7000)
    expect(r.transport.listenedMs()).toBe(37_000)
    expect(r.service.sessions).toHaveLength(1)
    expect(r.service.requests.map((x) => x.startMs)).toEqual([0, 600_000])
  })
})

describe('R3 (2): switching windows carries the counter over and does not count the gap', () => {
  it('preloads the next window at the previous end, switches on ended, and counts the join gap as nothing', async () => {
    const r = rig()
    await play(r)
    await r.advance(100_000)
    reach(r, 100)
    expect(r.service.requests).toHaveLength(1) // 140 s left: not yet
    const first = r.current()
    reach(r, 200) // 40 s left: ask for the next window
    await flush()
    expect(r.service.requests.map((x) => x.startMs)).toEqual([0, WINDOW_MS]) // it starts where the first one ends
    await r.advance(40_000)
    first.fire('ended')
    first.paused = true // the browser has stopped it
    await r.advance(3000) // the gap between the windows
    const second = r.audio.find((a) => a !== first)
    expect(second?.src).toBe(`/clip/s1/${String(WINDOW_MS)}`)
    expect(second?.paused).toBe(false) // switched on ended, no user gesture
    expect(r.transport.observe().status).toBe('starting')
    second?.fire('playing')
    await r.advance(10_000)
    expect(r.transport.listenedMs()).toBe(150_000) // 100 + 40 + 10, not the 3 s gap
    expect(r.transport.observe()).toMatchObject({ status: 'playing', transportMs: WINDOW_MS })
    expect(r.service.sessions).toHaveLength(1)
  })

  it('waits for a next window that is still rendering, and switches the moment it arrives', async () => {
    const r = rig()
    r.service.hold.add(WINDOW_MS)
    await play(r)
    reach(r, 200)
    await flush()
    await r.advance(40_000)
    const first = r.current()
    first.fire('ended')
    first.paused = true
    await r.advance(5000)
    expect(r.transport.observe()).toMatchObject({ status: 'starting', reason: 'next_part' })
    expect(r.transport.listenedMs()).toBe(40_000)
    r.service.held[0]?.release({ url: '/clip/s1/next', startMs: WINDOW_MS, endMs: 2 * WINDOW_MS })
    await flush()
    const second = r.audio.find((a) => a !== first)
    expect(second?.paused).toBe(false)
    second?.fire('playing')
    await r.advance(1000)
    expect(r.transport.listenedMs()).toBe(41_000)
  })

  it('ends the session at the end of the class', async () => {
    const r = rig({ mixEndMs: 100_000 })
    await play(r)
    await r.advance(5000)
    const only = r.current()
    only.fire('ended')
    expect(r.transport.observe().status).toBe('idle')
    expect(r.service.requests).toHaveLength(1) // there is no window after the last
  })
})

describe('R3 (3): waiting and stalled stop the counter until playing', () => {
  it('counts neither the buffering nor the stall', async () => {
    const r = rig()
    await play(r)
    await r.advance(5000)
    r.current().fire('waiting')
    expect(r.transport.observe().status).toBe('starting')
    await r.advance(7000)
    r.current().fire('stalled')
    await r.advance(2000)
    expect(r.transport.listenedMs()).toBe(5000)
    r.current().fire('playing')
    await r.advance(3000)
    expect(r.transport.listenedMs()).toBe(8000)
    expect(r.transport.observe().status).toBe('playing')
  })

  it('every stop event stops it, and a second `playing` while it runs adds nothing', async () => {
    for (const stop of ['pause', 'seeking', 'ended', 'emptied']) {
      const r = rig()
      await play(r)
      await r.advance(2000)
      r.current().fire('playing') // a repeat while running
      await r.advance(1000)
      r.current().fire(stop)
      await r.advance(9000)
      expect(r.transport.listenedMs(), stop).toBe(3000)
    }
  })

  it('ignores the events of the element that is not playing', async () => {
    const r = rig()
    await play(r)
    await r.advance(1000)
    const idle = r.audio.find((a) => a !== r.current())
    idle?.fire('pause')
    idle?.fire('waiting')
    await r.advance(1000)
    expect(r.transport.listenedMs()).toBe(2000)
  })
})

describe('R3 (4): Pause ends the session, and Play restarts at 0', () => {
  it('starts a new session with the counter at 0', async () => {
    const r = rig()
    await play(r)
    await r.advance(10_000)
    reach(r, 10)
    await r.transport.pause()
    expect(r.transport.observe()).toMatchObject({ status: 'paused', transportMs: 10_000 })
    expect(r.audio.every((a) => a.paused)).toBe(true)
    await r.advance(60_000)
    expect(r.transport.listenedMs()).toBe(10_000) // nothing runs after a Pause
    await play(r, 10_000) // Play from the position it stopped at
    expect(r.transport.listenedMs()).toBe(0)
    expect(r.service.sessions).toEqual(['s1', 's2'])
    expect(r.service.requests.at(-1)).toEqual({ sessionId: 's2', startMs: 10_000 })
    await r.advance(4000)
    expect(r.transport.listenedMs()).toBe(4000)
  })
})

describe('R3 (5): auto-pause exactly at 15:00 accumulated', () => {
  it('across several intervals, windows, seeks and stalls, with the message', async () => {
    const r = rig()
    let expected = 0
    const listen = async (ms: number) => {
      await r.advance(ms)
      expected += ms
      expect(r.transport.listenedMs()).toBe(expected)
    }
    await play(r)
    await listen(120_000)
    r.current().fire('waiting')
    await r.advance(10_000) // a stall: not counted
    r.current().fire('playing')
    await listen(60_000)
    await seek(r, 30_000) // a seek: not counted
    await listen(50_000)
    reach(r, 215) // 25 s left: the next window is asked for
    await flush()
    await listen(25_000)
    const first = r.current()
    first.fire('ended')
    first.paused = true
    await r.advance(2000) // the join gap
    r.audio.find((a) => a !== first)?.fire('playing')
    await listen(200_000) // 455 s so far
    await seek(r, 300_000) // back inside the second window
    await listen(240_000) // 695 s
    r.current().fire('pause') // the user's own pause key and play again
    await r.advance(30_000)
    r.current().fire('playing')
    await listen(199_000) // 894 s: still playing
    expect(r.transport.observe().status).toBe('playing')
    await listen(5000) // 899 s
    expect(r.transport.observe().status).toBe('playing')
    await r.advance(1000) // 900 s: the tick finds the limit
    expect(expected + 1000).toBe(LISTEN_LIMIT_MS)
    expect(r.transport.observe()).toMatchObject({ status: 'paused', reason: 'limit' })
    expect(r.audio.every((a) => a.paused)).toBe(true)
    await r.advance(10_000)
    expect(r.transport.listenedMs()).toBe(LISTEN_LIMIT_MS) // it has stopped adding
    // Play starts a new session with the counter at 0.
    await play(r, r.transport.observe().transportMs ?? 0)
    expect(r.transport.listenedMs()).toBe(0)
    expect(r.service.sessions).toHaveLength(2)
  })

  it('does not stop a second early: 14:59 plays on', async () => {
    const r = rig()
    await play(r)
    await r.advance(LISTEN_LIMIT_MS - 1000)
    expect(r.transport.observe().status).toBe('playing')
    expect(r.transport.listenedMs()).toBe(LISTEN_LIMIT_MS - 1000)
  })

  it('counts an interval that is still running when the tick checks', async () => {
    const r = rig()
    await play(r)
    await r.advance(400_000)
    r.current().fire('pause')
    await r.advance(50_000)
    r.current().fire('playing')
    await r.advance(500_000) // 900 s in all
    expect(r.transport.observe()).toMatchObject({ status: 'paused', reason: 'limit' })
  })
})

describe('the cursor is only what observe() says', () => {
  it('is not playing until the element says playing, and is read from the element, not from the request', async () => {
    const r = rig()
    expect(r.transport.observe().status).toBe('idle')
    expect(hasCursor(r.transport.observe())).toBe(false)
    await r.transport.audition({ ...PLAY, classMs: 30_000 })
    expect(r.transport.observe()).toMatchObject({ status: 'starting', transportMs: 30_000 })
    r.current().fire('playing')
    expect(r.transport.observe()).toMatchObject({ status: 'playing', transportMs: 30_000 })
    r.current().currentTime = 12.5
    expect(r.transport.observe().transportMs).toBe(42_500)
    expect(hasCursor(r.transport.observe())).toBe(true)
    r.current().currentTime = 20 // the element moved on by itself
    expect(r.transport.observe().transportMs).toBe(50_000)
  })

  it('says it is a rendered transport, approximate unless the mix was chosen', () => {
    const r = rig()
    expect(r.transport.capabilities).toMatchObject({ kind: 'rendered', exactMix: false, crossfades: true })
  })

  it('refuses a position outside the class', async () => {
    const r = rig({ mixEndMs: 100_000 })
    expect(await r.transport.audition({ ...PLAY, classMs: 100_000 })).toEqual({ ok: false, reason: 'outside_class' })
    expect(await r.transport.audition({ ...PLAY, classMs: -1 })).toEqual({ ok: false, reason: 'outside_class' })
    expect(r.service.sessions).toHaveLength(0)
  })
})
