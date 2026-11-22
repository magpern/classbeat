import { describe, expect, it } from 'vitest'
import { en } from '../i18n/en'
import type { SpotifyView } from './spotifyConnectTransport'
import { cursorSentence, noteSentence, stateSentence } from './spotifyStatusModel'
import type { TransportState } from './transport'

const view = (state: Partial<TransportState>, extra: Partial<SpotifyView> = {}): SpotifyView => ({
  state: { status: 'idle', observedAt: 0, uncertaintyMs: 0, ...state },
  classMs: null,
  inOverlap: false,
  band: null,
  note: null,
  device: null,
  failure: null,
  ...extra,
})

describe('stateSentence', () => {
  it.each([
    [{ status: 'idle' }, 'preview.state.idle'],
    [{ status: 'idle', reason: 'ended' }, 'preview.state.ended'],
    [{ status: 'starting' }, 'preview.state.starting'],
    [{ status: 'playing' }, 'preview.state.playing'],
    [{ status: 'paused' }, 'preview.state.paused'],
    [{ status: 'external', reason: 'other_track' }, 'preview.state.external'],
    [{ status: 'unaligned', reason: 'timing_unusable' }, 'preview.state.unaligned'],
    [{ status: 'outside_window', reason: 'before' }, 'preview.state.outside_before'],
    [{ status: 'outside_window', reason: 'after' }, 'preview.state.outside_after'],
  ] as const)('%j has its own sentence', (state, key) => {
    const sentence = stateSentence(view(state), 'Kitchen')
    expect(sentence.key).toBe(key)
    expect(en).toHaveProperty([sentence.key])
  })

  it('names the device while playing or paused', () => {
    expect(stateSentence(view({ status: 'playing' }), 'Kitchen').params).toEqual({ device: 'Kitchen' })
  })

  it.each(['no_device', 'unauthorized', 'rate_limited', 'premium_or_allowlist', 'weird_new_code'])('a failure (%s) is a sentence, never its code', (reason) => {
    const failure = { reason, status: 409, retryAfterS: reason === 'rate_limited' ? 12 : null }
    const sentence = stateSentence(view({ status: reason === 'no_device' ? 'no_device' : 'error', reason }, { failure }), 'x')
    expect(sentence.tone).toBe('error')
    expect(sentence.key).toMatch(/^preview\.err\./)
    expect(en).toHaveProperty([sentence.key])
    // the person reads the catalogue's words for the key, never the provider's code
    expect(Object.values(sentence.params).map(String)).not.toContain(reason)
    expect(en[sentence.key as keyof typeof en]).not.toContain(reason)
  })

  it('a rate limit says in how many seconds', () => {
    const failure = { reason: 'rate_limited', status: 429, retryAfterS: 12 }
    expect(stateSentence(view({ status: 'error', reason: 'rate_limited' }, { failure }), 'x')).toMatchObject({ key: 'preview.err.rate_limited_for', params: { seconds: 12 } })
  })
})

describe('noteSentence and cursorSentence', () => {
  it('notes a seek and a device switch', () => {
    expect(noteSentence(view({}, { note: 'seek_detected' }), 'x')?.key).toBe('preview.state.seek')
    expect(noteSentence(view({}, { note: 'device_switched' }), 'Phone')).toMatchObject({ key: 'preview.state.device', params: { device: 'Phone' } })
    expect(noteSentence(view({}), 'x')).toBeNull()
  })

  it('has no cursor sentence without a cursor', () => {
    expect(cursorSentence(view({}), '0:00').key).toBe('preview.cursor.none')
  })

  it('labels the band, and says "approximate" inside an overlap', () => {
    const at = view({ status: 'playing', uncertaintyMs: 420 }, { classMs: 195_000, band: { fromMs: 194_580, toMs: 195_420 } })
    expect(cursorSentence(at, '3:15')).toMatchObject({ key: 'preview.cursor.at', params: { time: '3:15', ms: 420 } })
    expect(cursorSentence({ ...at, inOverlap: true }, '3:15')).toMatchObject({ key: 'preview.cursor.overlap', params: { ms: 420 } })
  })
})
