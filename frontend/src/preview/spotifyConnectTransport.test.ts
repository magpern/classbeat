import { beforeEach, describe, expect, it } from 'vitest'
import { FakeClock, FakePlayer, flush } from '../test/spotifyPlayerFake'
import { DEFAULT_BACKOFF_S, MAX_BACKOFF_S, NOTE_MS, SpotifyConnectTransport, STARTING_GRACE_MS } from './spotifyConnectTransport'
import { buildSpotifyModel, type SpotifySong } from './timeModel'
import type { PreviewFailure } from '../api/spotifyPreview'

// The Spotify transport against a fake player (no network). The cursor must come from what the
// player was OBSERVED to do. Each guard below has a test that fails if the guard is removed.

const SEC = 1000
const song = (piece: number, extra: Partial<SpotifySong> = {}): SpotifySong => ({
  piece,
  label: `Song ${String(piece + 1)}`,
  lengthMs: 200 * SEC,
  alignmentOffsetMs: 1000,
  correctionOffsetMs: 0,
  timingClass: 'approximate',
  ...extra,
})
// S0 = 0..200 s, S1 = 190..390 s, S2 = 380..580 s (a 10 s crossfade).
const songs = (): SpotifySong[] => [song(0), song(1, { alignmentOffsetMs: -500 }), song(2)]

let clock: FakeClock
let player: FakePlayer

function make(list: SpotifySong[] = songs(), startingGraceMs?: number): SpotifyConnectTransport {
  return new SpotifyConnectTransport({
    api: player,
    model: buildSpotifyModel(list, 10 * SEC),
    now: clock.now,
    ...(startingGraceMs === undefined ? {} : { startingGraceMs }),
  })
}

/** A user gesture: play `piece` at `sourceMs` of its analysed file, and let the call finish. */
async function play(transport: SpotifyConnectTransport, piece: number, sourceMs: number, classMs = 0) {
  const done = transport.audition({ piece, sourceMs, classMs })
  await clock.advance(200)
  return done
}

async function poll(transport: SpotifyConnectTransport, wait = 200): Promise<void> {
  const done = transport.poll()
  await clock.advance(wait)
  await done
}

beforeEach(() => {
  clock = new FakeClock()
  player = new FakePlayer(clock)
})

describe('nothing plays by itself', () => {
  it('starts idle, polls nothing and reports no cursor', async () => {
    const transport = make()
    expect(transport.observe().status).toBe('idle')
    expect(transport.wantsPolling()).toBe(false)
    await clock.advance(60_000)
    expect(player.log).toEqual([])
    expect(transport.capabilities).toMatchObject({ kind: 'spotify', crossfades: false, exactMix: false })
  })

  it('does not resume what the player is doing: the cursor needs a play from this page first', async () => {
    player.status = 'playing'
    player.pieces = [0]
    player.seekTo(30_000)
    const transport = make()
    await poll(transport)
    expect(transport.observe().status).toBe('idle')
    expect(transport.view().classMs).toBeNull()
  })

  it('asks Spotify for the song at the position of its recording: source + alignment + correction', async () => {
    const transport = make([song(0, { alignmentOffsetMs: 1500, correctionOffsetMs: -250 })])
    await play(transport, 0, 10_000)
    expect(player.log.find((c) => c.call === 'audition')?.arg).toBe(11_250)
  })
})

describe('the cursor is drawn only from observe()', () => {
  it('a play that was accepted is "starting" with no position, whatever was requested', async () => {
    const transport = make()
    await expect(play(transport, 0, 5000, 5000)).resolves.toEqual({ ok: true })
    const state = transport.observe()
    expect(state.status).toBe('starting')
    expect(state.transportMs).toBeUndefined()
    expect(transport.view().classMs).toBeNull()
  })

  it('shows where the player was OBSERVED, not where the play asked it to go', async () => {
    player.landAt = 8000 // Spotify lands later than the 6000 ms asked for (5000 + 1000 alignment)
    const transport = make()
    await play(transport, 0, 5000, 5000)
    await poll(transport)
    const view = transport.view()
    expect(view.state.status).toBe('playing')
    // Observed: where the player really is now, less the 1000 ms alignment (S0 = 0). Not the 5000 asked.
    expect(view.state.transportMs).toBeGreaterThan(8000)
    expect(view.classMs).not.toBeNull()
    expect(Math.abs((view.classMs ?? 0) - (player.position() - 1000))).toBeLessThan(100)
    expect(Math.abs((view.classMs ?? 0) - 5000)).toBeGreaterThan(1500)
  })

  it('moves on with the clock between polls', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    await poll(transport)
    const first = transport.view().classMs ?? 0
    await clock.advance(2000)
    expect((transport.view().classMs ?? 0) - first).toBe(2000)
  })

  it('a paused player holds its position and its uncertainty does not grow', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    await poll(transport)
    player.userPauses()
    await poll(transport)
    const a = transport.view()
    await clock.advance(5000)
    const b = transport.view()
    expect(a.state.status).toBe('paused')
    expect(b.state.status).toBe('paused')
    expect(b.classMs).toBe(a.classMs)
    expect(b.state.uncertaintyMs).toBe(a.state.uncertaintyMs)
  })

  it('pause() sends a pause and takes the position from what the player answers', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    await poll(transport)
    const done = transport.pause()
    await clock.advance(300)
    await done
    expect(transport.observe().status).toBe('paused')
    expect(player.log.some((c) => c.call === 'pause')).toBe(true)
  })
})

describe('the states', () => {
  it('autoplay or another track is "external" after the play had time to start, with no cursor', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    await poll(transport)
    expect(transport.observe().status).toBe('playing')
    player.autoplay()
    await poll(transport)
    const view = transport.view()
    expect(view.state.status).toBe('external')
    expect(view.classMs).toBeNull()
    expect(view.state.transportMs).toBeUndefined()
    expect(transport.wantsPolling()).toBe(false)
  })

  it('a stale state straight after a play is still "starting", then "external" once the grace is over', async () => {
    player.status = 'external'
    player.pieces = []
    const transport = make()
    player.latency('audition', { out: 10, back: 10 })
    // The player has not switched yet when the first poll reads it.
    player.status = 'external'
    const done = transport.audition({ piece: 0, sourceMs: 0, classMs: 0 })
    await clock.advance(20)
    await done
    player.pieces = []
    player.status = 'external'
    await poll(transport)
    expect(transport.observe().status).toBe('starting')
    await clock.advance(STARTING_GRACE_MS)
    await poll(transport)
    expect(transport.observe().status).toBe('external')
  })

  it('a paused read of the requested song while the play is unconfirmed stays "starting" and keeps polling', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    player.status = 'paused' // a replay of a song just paused: Spotify has not started it yet
    await poll(transport)
    expect(transport.observe().status).toBe('starting')
    expect(transport.wantsPolling()).toBe(true)
    player.status = 'playing'
    await poll(transport)
    expect(transport.observe().status).toBe('playing')
    expect(transport.wantsPolling()).toBe(true)
  })

  it('a paused read of the requested song after the grace settles to "paused" and stops polling', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    player.status = 'paused'
    await poll(transport)
    await clock.advance(STARTING_GRACE_MS)
    await poll(transport)
    expect(transport.observe().status).toBe('paused')
    expect(transport.wantsPolling()).toBe(false)
  })

  it('a song whose timing is "unusable" is "unaligned": it plays, but there is no cursor', async () => {
    const transport = make([song(0, { timingClass: 'unusable' })])
    await play(transport, 0, 5000)
    await poll(transport)
    const view = transport.view()
    expect(view.state.status).toBe('unaligned')
    expect(view.classMs).toBeNull()
    expect(view.state.transportMs).toBeUndefined()
    expect(transport.wantsPolling()).toBe(true) // the player plays: keep watching
  })

  it('a song with no alignment is "unaligned" too', async () => {
    const transport = make([song(0, { alignmentOffsetMs: null })])
    await play(transport, 0, 5000)
    await poll(transport)
    expect(transport.observe().status).toBe('unaligned')
  })

  it('a position before the class (an intro) is "outside_window", and after it too', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    player.seekTo(400) // 400 ms in the recording, alignment 1000: before the class starts
    await poll(transport)
    const intro = transport.view()
    expect(intro.state.status).toBe('outside_window')
    expect(intro.state.reason).toBe('before')
    expect(intro.classMs).toBeNull()
    player.seekTo(210 * SEC)
    await poll(transport)
    expect(transport.view().state.reason).toBe('after')
  })

  it('inside an overlap the cursor has a labelled band: half the round trip plus the age of the poll', async () => {
    const transport = make()
    // 195 s of song 2 (piece 1, alignment -500) is class time 190 s + 195 s... use piece 1 at 5 s: class 195 s, inside the overlap [190, 200).
    player.latency('audition', { out: 100, back: 100 })
    await play(transport, 1, 5000, 195 * SEC)
    player.latency('state', { out: 400, back: 600 }) // a 1000 ms round trip
    await poll(transport, 1000)
    await clock.advance(250) // the poll is 250 ms old
    const view = transport.view()
    expect(view.state.status).toBe('playing')
    expect(view.inOverlap).toBe(true)
    expect(view.state.uncertaintyMs).toBe(500 + 250)
    expect(view.band).not.toBeNull()
    const at = view.classMs ?? 0
    expect(view.band).toEqual({ fromMs: at - 750, toMs: at + 750 })
  })

  it('outside an overlap the same position is not labelled as one', async () => {
    const transport = make()
    await play(transport, 0, 100 * SEC)
    await poll(transport)
    expect(transport.view().inOverlap).toBe(false)
  })
})

describe('failures', () => {
  const fail = (reason: string, status = 409, retryAfterS: number | null = null): PreviewFailure => ({ reason, status, retryAfterS })

  it.each([
    ['no_device', 'no_device'],
    ['unauthorized', 'error'],
    ['premium_or_allowlist', 'error'],
    ['needs_reconnect', 'error'],
    ['preview_not_granted', 'error'],
    ['rate_limited', 'error'],
    ['upstream_unavailable', 'error'],
    ['something_new', 'error'],
  ])('a refused play (%s) is "%s" with its reason, and nothing starts', async (reason, status) => {
    player.failNext('audition', fail(reason, reason === 'unauthorized' ? 401 : 409))
    const transport = make()
    await expect(play(transport, 0, 5000)).resolves.toEqual({ ok: false, reason })
    const view = transport.view()
    expect(view.state.status).toBe(status)
    expect(view.state.reason).toBe(reason)
    expect(view.failure?.reason).toBe(reason)
    expect(view.classMs).toBeNull()
    expect(transport.wantsPolling()).toBe(false)
  })

  it('a 429 keeps its Retry-After: no poll before it, polling goes on after it', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    player.failNext('state', fail('rate_limited', 429, 7))
    await poll(transport)
    expect(transport.view().state.status).toBe('error')
    expect((transport.notBefore() ?? 0) - clock.t).toBeGreaterThan(6500)
    expect((transport.notBefore() ?? 0) - clock.t).toBeLessThanOrEqual(7000)
    expect(transport.wantsPolling()).toBe(true)
    await clock.advance(7000)
    await poll(transport)
    expect(transport.view().state.status).toBe('playing')
    expect(transport.notBefore()).toBeNull()
  })

  it('a 429 without a Retry-After backs off, longer each time in a row, and starts over after an answer', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    const wait = () => (transport.notBefore() ?? 0) - clock.t
    player.failNext('state', fail('rate_limited', 429))
    await poll(transport)
    expect(wait()).toBeGreaterThan(DEFAULT_BACKOFF_S * SEC - 500) // not every second
    expect(wait()).toBeLessThanOrEqual(DEFAULT_BACKOFF_S * SEC)
    await clock.advance(DEFAULT_BACKOFF_S * SEC)
    player.failNext('state', fail('rate_limited', 429))
    await poll(transport)
    expect(wait()).toBeGreaterThan(2 * DEFAULT_BACKOFF_S * SEC - 500)
    for (let i = 0; i < 6; i += 1) {
      await clock.advance(MAX_BACKOFF_S * SEC)
      player.failNext('state', fail('rate_limited', 429))
      await poll(transport)
    }
    expect(wait()).toBeLessThanOrEqual(MAX_BACKOFF_S * SEC) // capped
    await clock.advance(MAX_BACKOFF_S * SEC)
    await poll(transport) // an answer
    expect(transport.notBefore()).toBeNull()
    player.failNext('state', fail('rate_limited', 429))
    await poll(transport)
    expect(wait()).toBeLessThanOrEqual(DEFAULT_BACKOFF_S * SEC) // the streak started over
  })

  it('a failure that needs the person stops the polling', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    player.failNext('state', fail('needs_reconnect'))
    await poll(transport)
    expect(transport.wantsPolling()).toBe(false)
    expect(transport.view().state.reason).toBe('needs_reconnect')
  })

  it('a device switch keeps the cursor and says so for a few seconds', async () => {
    const transport = make()
    await play(transport, 0, 5000)
    await poll(transport)
    player.device = { name: 'Phone', type: 'Smartphone' }
    await poll(transport)
    const view = transport.view()
    expect(view.state.status).toBe('playing')
    expect(view.note).toBe('device_switched')
    expect(view.device).toEqual({ name: 'Phone', type: 'Smartphone' })
    expect(view.classMs).not.toBeNull()
    await clock.advance(NOTE_MS + 1)
    expect(transport.view().note).toBeNull()
  })

  it.each(['audition', 'pause', 'state'] as const)('a %s call that throws is an error the page shows, never a silent success', async (call) => {
    const transport = make()
    if (call !== 'audition') await play(transport, 0, 5000)
    player.throwOn(call)
    const done = call === 'audition' ? transport.audition({ piece: 0, sourceMs: 0, classMs: 0 }) : call === 'pause' ? transport.pause() : transport.poll()
    await clock.advance(500)
    const outcome = await done
    if (call === 'audition') expect(outcome).toEqual({ ok: false, reason: 'unknown' })
    const view = transport.view()
    expect(view.state.status).toBe('error')
    expect(view.failure?.reason).toBe('unknown')
    expect(view.classMs).toBeNull()
  })
})

describe('answers that arrive out of order', () => {
  it.each([true, false])('an older answer never overwrites a newer one (Spotify timestamps: %s)', async (timestamps) => {
    player.timestamps = timestamps
    const transport = make()
    await play(transport, 0, 10_000)
    await poll(transport)
    // Poll A is slow; before it returns the player is seeked far ahead and poll B overtakes it.
    player.latency('state', { out: 100, back: 2900 }, { out: 100, back: 100 })
    const a = transport.poll()
    await clock.advance(1000)
    player.seekTo(150 * SEC)
    const b = transport.poll()
    await clock.advance(200)
    await b
    const afterB = transport.view().classMs
    expect(Math.abs((afterB ?? 0) - 149 * SEC)).toBeLessThan(1500)
    await clock.advance(3000) // A arrives now, and it saw the old position
    await a
    const view = transport.view()
    expect(Math.abs((view.classMs ?? 0) - (150 * SEC - 1000 + (clock.t - 1200)))).toBeLessThan(1500)
    expect((view.classMs ?? 0)).toBeGreaterThan(140 * SEC)
  })

  it('an answer asked before a new play is dropped, not shown as the new play', async () => {
    const transport = make()
    await play(transport, 0, 10_000)
    await poll(transport)
    player.latency('state', { out: 100, back: 2000 })
    const old = transport.poll() // asks about the song playing now
    await clock.advance(300)
    player.latency('audition', { out: 10, back: 10 })
    const again = transport.audition({ piece: 1, sourceMs: 5000, classMs: 195 * SEC })
    await clock.advance(100)
    await again
    await clock.advance(3000)
    await old
    // The old answer (piece 0 playing) must not count for the new play of piece 1.
    expect(transport.view().state.status).toBe('starting')
    expect(transport.view().classMs).toBeNull()
  })

  it('a failure asked before a new play does not show on the new play', async () => {
    const transport = make()
    await play(transport, 0, 10_000)
    await poll(transport)
    player.latency('state', { out: 100, back: 2000 })
    player.failNext('state', { reason: 'rate_limited', status: 429, retryAfterS: 30 })
    const old = transport.poll()
    await clock.advance(300)
    player.latency('audition', { out: 10, back: 10 })
    const again = transport.audition({ piece: 1, sourceMs: 5000, classMs: 195 * SEC })
    await clock.advance(100)
    await again
    await clock.advance(3000)
    await old
    expect(transport.view().failure).toBeNull()
    expect(transport.notBefore()).toBeNull()
    expect(transport.view().state.status).toBe('starting')
  })

  it('does not pile asks up behind a slow answer', async () => {
    const transport = make()
    await play(transport, 0, 10_000)
    player.latency('state', { out: 100, back: 9000 })
    void transport.poll()
    void transport.poll()
    void transport.poll()
    await flush()
    expect(player.log.filter((c) => c.call === 'state')).toHaveLength(2)
  })
})

describe('seek detection', () => {
  it('a jump of more than 1.5 s from where the player should be is noted, and the newest position is used', async () => {
    const transport = make()
    await play(transport, 0, 10_000)
    await poll(transport)
    await clock.advance(1000)
    player.seekTo(player.position() + 20 * SEC)
    await poll(transport)
    const view = transport.view()
    expect(view.note).toBe('seek_detected')
    expect(Math.abs((view.classMs ?? 0) - (player.position() - 1000))).toBeLessThan(300)
  })

  it('a small drift is not a seek', async () => {
    const transport = make()
    await play(transport, 0, 10_000)
    await poll(transport)
    await clock.advance(1000)
    player.seekTo(player.position() + 1200)
    await poll(transport)
    expect(transport.view().note).toBeNull()
  })
})
