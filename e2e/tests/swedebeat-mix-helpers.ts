import { execFileSync } from 'node:child_process'
import { fakeControl } from './design-helpers'
import { compose } from './spotify-helpers'

// Swedebeat mix design (ADR 0016 section 3, A6): the fake MOWL's mix endpoints and test control.
// Public test values only; nothing here is a real link or signature.

export const CACHED_MIX_ID = 'e2e-mix-0002'
export const CACHED_MIX_NAME = 'E2E Cached Mix'
export const UNCACHED_MIX_ID = 'e2e-mix-0001'
export const UNCACHED_MIX_NAME = 'E2E Swedebeat Mix'
export const PLANTED_PLAYLIST_ID = 9002

export interface SwedebeatMixFakeState {
  lists: number
  attaches: number
  links: number
  attached: number
}

export const resetSwedebeatMixes = (): SwedebeatMixFakeState =>
  fakeControl('POST', 'swedebeat-arm') as SwedebeatMixFakeState

export const swedebeatMixState = (): SwedebeatMixFakeState =>
  fakeControl('GET', 'swedebeat-state') as SwedebeatMixFakeState

/** One SQL statement as the database superuser in the stack's db container. Constants only. */
export function sql(statement: string): string {
  return compose(['exec', '-T', 'db', 'psql', '-U', 'postgres', '-d', 'classbeat', '-At', '-c', statement]).trim()
}

function mapVersion(): number {
  const out = compose([
    'exec',
    '-T',
    'worker',
    'python',
    '-c',
    'from mowl import music; print(music.MAP_VERSION)',
  ]).trim()
  return Number(out)
}

/** Seed a cache hit for one mix: owned link row plus map row for the user's MOWL connection. */
export function seedCachedMix(ownerId: string, connectionId: string, mixId: string): void {
  const version = mapVersion()
  const map = JSON.stringify({
    version,
    duration: 120.0,
    // The shape of music.analyze_signal's map: every song and event carries its 1-based `song`.
    songs: [
      {
        song: 1,
        start: 0.0,
        end: 120.0,
        bpm: 124.0,
        first_downbeat: 0.4,
        first_phrase: 0.4,
        phrase_bars: 8,
        grid_score: 0.9,
        beat1_margin: 2.0,
      },
    ],
    events: [{ t: 10.0, precise: 10.0, kind: 'drop', strength: 0.8, song: 1 }],
  }).replaceAll("'", "''")
  sql(
    `INSERT INTO mowl_playlist_links (owner_id, source, source_id, playlist_id) VALUES ('${ownerId}', 'swedebeat', '${mixId}', ${String(PLANTED_PLAYLIST_ID)}) ON CONFLICT (owner_id, source, source_id) DO UPDATE SET playlist_id = EXCLUDED.playlist_id`,
  )
  sql(
    `INSERT INTO swedebeat_mix_maps (owner_id, mix_id, mowl_connection_id, playlist_id, name, duration, listed_duration, map) VALUES ('${ownerId}', '${mixId}', '${connectionId}', ${String(PLANTED_PLAYLIST_ID)}, '${CACHED_MIX_NAME}', 120, 120, '${map}'::jsonb) ON CONFLICT (owner_id, mix_id) DO UPDATE SET mowl_connection_id = EXCLUDED.mowl_connection_id, playlist_id = EXCLUDED.playlist_id, map = EXCLUDED.map, created_at = now()`,
  )
}

export function ownerIdForEmail(email: string): string {
  return sql(`SELECT id FROM users WHERE email = '${email.replaceAll("'", "''")}'`)
}

/** Recent worker log lines (no secrets expected for the media fetch success path). */
export function workerLogTail(lines = 200): string {
  return execFileSync(
    'docker',
    ['compose', 'logs', '--tail', String(lines), 'worker'],
    { cwd: new URL('../..', import.meta.url), encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] },
  )
}
