import { describe, expect, it } from 'vitest'
import { SilentTransport } from './silentTransport'
import { hasCursor, type PreviewTransport } from './transport'

function make(total = 600_000) {
  const clock = { at: 1_000 }
  const transport: PreviewTransport = new SilentTransport({ totalMs: total, now: () => clock.at })
  return { clock, transport }
}

describe('SilentTransport', () => {
  it('declares itself silent: no exact mix, no crossfades', () => {
    expect(make().transport.capabilities).toEqual({ exactMix: false, crossfades: false, seekGranularityMs: 1, kind: 'silent' })
  })

  it('starts idle with no position and no cursor', () => {
    const state = make().transport.observe()
    expect(state).toEqual({ status: 'idle', observedAt: 1_000, uncertaintyMs: 0 })
    expect(hasCursor(state)).toBe(false)
  })

  it('moves only with the clock: the cursor is what observe() says, not what audition asked', async () => {
    const { clock, transport } = make()
    expect(await transport.audition({ piece: 2, sourceMs: 5, classMs: 30_000 })).toEqual({ ok: true })
    expect(transport.observe()).toMatchObject({ status: 'playing', piece: 2, transportMs: 30_000, observedAt: 1_000 })
    clock.at += 2_500
    expect(transport.observe()).toMatchObject({ status: 'playing', transportMs: 32_500, observedAt: 3_500 })
  })

  it('freezes on pause and resumes from the same place', async () => {
    const { clock, transport } = make()
    await transport.audition({ piece: 0, sourceMs: 0, classMs: 10_000 })
    clock.at += 1_000
    await transport.pause()
    clock.at += 60_000
    expect(transport.observe()).toMatchObject({ status: 'paused', transportMs: 11_000 })
    await transport.audition({ piece: 0, sourceMs: 0, classMs: 11_000 })
    clock.at += 500
    expect(transport.observe()).toMatchObject({ status: 'playing', transportMs: 11_500 })
  })

  it('goes idle when the clock runs past the end of the class', async () => {
    const { clock, transport } = make(5_000)
    await transport.audition({ piece: 0, sourceMs: 0, classMs: 4_000 })
    clock.at += 2_000
    expect(transport.observe().status).toBe('idle')
    expect(transport.observe().transportMs).toBeUndefined()
  })

  it('refuses an instant outside the class and stays where it was', async () => {
    const { transport } = make(5_000)
    expect(await transport.audition({ piece: 0, sourceMs: 0, classMs: 5_000 })).toEqual({ ok: false, reason: 'outside_class' })
    expect(await transport.audition({ piece: 0, sourceMs: 0, classMs: -1 })).toEqual({ ok: false, reason: 'outside_class' })
    expect(transport.observe().status).toBe('idle')
  })

  it('pause with nothing playing changes nothing', async () => {
    const { transport } = make()
    await transport.pause()
    expect(transport.observe().status).toBe('idle')
  })
})
