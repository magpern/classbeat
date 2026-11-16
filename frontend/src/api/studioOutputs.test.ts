import { describe, expect, it } from 'vitest'
import { DEFAULT_OUTPUTS, toggleOutput } from './studioOutputs'

describe('the output choice', () => {
  it('starts with Spotify and the SwedeBeat playlist', () => {
    expect(DEFAULT_OUTPUTS).toEqual(['spotify_playlist', 'swedebeat_playlist'])
  })

  it('MOWL on adds the mix, and the mix cannot go while MOWL is on', () => {
    const withMowl = toggleOutput([...DEFAULT_OUTPUTS], 'mowl', true)
    expect(withMowl).toEqual(['spotify_playlist', 'swedebeat_playlist', 'swedebeat_mix', 'mowl'])
    expect(toggleOutput(withMowl, 'swedebeat_mix', false)).toEqual(['spotify_playlist', 'swedebeat_playlist'])
  })

  it('keeps the canonical order and never repeats an output', () => {
    expect(toggleOutput(['swedebeat_playlist'], 'spotify_playlist', true)).toEqual(['spotify_playlist', 'swedebeat_playlist'])
    expect(toggleOutput(['spotify_playlist'], 'spotify_playlist', true)).toEqual(['spotify_playlist'])
  })
})

describe('a job a studio route started', () => {
  it('is followed by its job_id, as the routes answer (StudioJobOut)', async () => {
    const { fakeApi, json } = await import('../test/fakeApi')
    const { chooseOutputs, startStudioDesign } = await import('./studioOutputs')
    const started = { job_id: '44444444-4444-4444-8444-444444444444', kind: 'produce_outputs', state: 'queued' }
    fakeApi({
      'POST /api/classes/c1/outputs': () => json(started, 201),
      'POST /api/classes/c1/studio-songs/r1/design': () => json({ ...started, kind: 'studio_design' }, 201),
    })
    const outputs = await chooseOutputs('c1', { revisionId: 'r1', contentHash: 'ab'.repeat(32), outputs: ['spotify_playlist'] }, 'k'.repeat(20))
    expect(outputs.kind).toBe('produce_outputs')
    expect(outputs.id).toBe(started.job_id)
    expect((await startStudioDesign('c1', 'r1', 'k'.repeat(20))).id).toBe(started.job_id)
  })
})
