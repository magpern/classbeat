import { describe, expect, it } from 'vitest'
import type { Brief, RunningBrief } from '../../api/jobs'
import type { Music, SongsAvailability, SwedebeatMixAvailability } from '../songs/MusicChoice'
import { formGate, planJob, type PlanValues } from './jobPlan'

const CYCLING: Brief = {
  length_min: 45,
  intensity: 'hard',
  climbs: 1,
  sprints: 0,
  races: 0,
  ftp_test: null,
  style: '',
  text: '',
}
const RUNNING: RunningBrief = { sport: 'running', length_min: 30, intensity: 'easy', style: '', text: '' }
const READY: SongsAvailability = { kind: 'ready', spotifyConnectionId: 's1' }
const CONSENT: SongsAvailability = { kind: 'needs_consent', spotifyConnectionId: 's1' }
const NEEDS: SongsAvailability = { kind: 'needs_spotify' }
const OFF: SongsAvailability = { kind: 'off' }
const NO_MIXES: SwedebeatMixAvailability = { kind: 'off' }
const MIXES: SwedebeatMixAvailability = { kind: 'ready' }

function values(music: Music, songs: SongsAvailability, extra: Partial<PlanValues> = {}): PlanValues {
  return {
    title: 'Tuesday',
    brief: CYCLING,
    music,
    running: false,
    noMusic: music === 'none',
    playlist: 'P1',
    mixId: 'M1',
    songs,
    mowlConnectionId: 'm1',
    ...extra,
  }
}

const PLAYLIST = { source: 'mowl_spotify_playlist', spotify_playlist_id: 'P1' } as const

describe('planJob', () => {
  const pick = { title: 'Tuesday', brief: CYCLING, mode: 'pick', mowl_connection_id: 'm1', spotify_connection_id: 's1' }
  const draft = (music: object, brief: Brief | RunningBrief = CYCLING) => ({
    kind: 'draft',
    draft: { title: 'Tuesday', brief, music, mowl_connection_id: 'm1' },
  })
  const source = {
    kind: 'source',
    pick: { ...pick, source: PLAYLIST },
    fallback: draft(PLAYLIST).draft,
  }
  const cases: [string, PlanValues, unknown][] = [
    ['own playlist, Spotify ready: a new playlist after the approval', values('playlist', READY), source],
    ['own playlist, working playlist not allowed: still a source job (no pool)', values('playlist', CONSENT), source],
    ['own playlist, Spotify not offered here: the write-free design', values('playlist', OFF), draft(PLAYLIST)],
    ['Pick for me, ready: a pick with no source', values('pick', READY), { kind: 'pick', pick }],
    ['a Swedebeat mix: a design on the mix', values('swedebeat_mix', READY), draft({ source: 'mowl_swedebeat_mix', mix_id: 'M1' })],
    ['No music: a soundless design', values('none', READY), draft({ source: 'none' })],
    [
      'a running class: the MOWL playlist, never a song job',
      values('playlist', READY, { running: true, brief: RUNNING }),
      draft(PLAYLIST, RUNNING),
    ],
  ]
  it.each(cases)('%s', (_name, given, expected) => {
    expect(planJob(given)).toEqual(expected)
  })
})

describe('formGate', () => {
  const connect = { need: 'connect', blocking: true }
  const cases: [string, PlanValues, SwedebeatMixAvailability, unknown][] = [
    ['no Spotify, a playlist: connect first', values('playlist', NEEDS), NO_MIXES, connect],
    ['no Spotify, a playlist, mixes offered: still connect first', values('playlist', NEEDS), MIXES, connect],
    ['no Spotify, "No music"', values('none', NEEDS), NO_MIXES, null],
    ['no Spotify, a Swedebeat mix', values('swedebeat_mix', NEEDS), MIXES, null],
    ['no Spotify, a running class', values('playlist', NEEDS, { running: true, brief: RUNNING }), NO_MIXES, null],
    ['working playlist not allowed, a playlist: the connection is enough', values('playlist', CONSENT), NO_MIXES, null],
    ['Spotify ready', values('playlist', READY), NO_MIXES, null],
    ['Spotify not offered here', values('playlist', OFF), NO_MIXES, null],
  ]
  it.each(cases)('%s', (_name, given, swedebeat, expected) => {
    expect(formGate(given, swedebeat)).toEqual(expected)
  })
})
