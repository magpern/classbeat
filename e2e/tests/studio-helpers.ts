import { expect, type Page } from '@playwright/test'
import { getJob } from './design-helpers'
import { useModelScript, type ModelScript } from './model-script'
import { sql } from './song-helpers'
import { CLIENT_A, connectSpotify, inContainer, playSpotify, spotifyDialog } from './spotify-helpers'
import { EMAIL_A, PASSWORD_A, fillSwedebeat, openSwedebeatDialog, setCatalogue, type Song } from './swedebeat-helpers'
import { seedProvenance } from './swedebeat-mix-helpers'

// Shared by the studio specs (ADR 0036, SR C8): a user with Spotify (and the working-playlist
// consent) and SwedeBeat connected and **no MOWL connection**, a studio pick started through the
// jobs API (the studio has no entry on the new-class form yet), and the analyses of the approved
// list seeded as the backend's design tests stand them in. Public test values only.

/** The brief the recorded studio design (`e2e_studio_design`) is written for. */
export const STUDIO_BRIEF = { length_min: 30, intensity: 'moderate', climbs: 1, sprints: 2 }

const WORDS = [
  'Climb', 'Summit', 'Ridge', 'Pulse', 'Tempo', 'Sprint', 'Valley', 'Thunder', 'Ember', 'Harbor',
  'Neon', 'Cascade', 'Drift', 'Horizon', 'Velvet', 'Comet', 'Granite', 'Willow', 'Signal', 'Orbit',
]
const ARTISTS = ['The Invented Band', 'Placeholder Collective', 'Test Pattern', 'Nobody Real', 'Fixture Five', 'Mock Orchestra']

/** The fake Spotify's catalogue track `i` (backend/tests/spotifycore.py `_catalogue`). */
function fakeSong(i: number): Song {
  return { title: `${WORDS[i % 20]!} ${WORDS[(i * 7 + 3) % 20]!} ${String(i)}`, artist: ARTISTS[i % 6]! }
}

/** The first ten tracks of the fake's artists `first` (0 The Invented Band, 1 Placeholder
 *  Collective, 2 Test Pattern, 3 Nobody Real), as one search answers them. */
function searched(...firsts: number[]): Song[] {
  const out: Song[] = []
  for (const first of firsts) for (let n = 0; n < 10; n += 1) out.push(fakeSong(first + 6 * n))
  return out
}

/** The two searches of `e2e_pick`: its whole pool of candidates. */
export function poolSongs(): Song[] {
  return searched(0, 1)
}

/** `e2e_studio_swap`'s catalogue: four of the pool's candidates (its refs 0, 2, 6 and 12) and
 *  every track of the swap round's two searches. */
export function swapCatalogue(): void {
  const pool = poolSongs()
  setCatalogue([...[0, 2, 6, 12].map((ref) => pool[ref]!), ...searched(2, 3)])
}

/** Every pool song is in the SwedeBeat catalogue except `missing` (by title). */
export function catalogueWithout(...missing: string[]): void {
  setCatalogue(poolSongs().filter((s) => !missing.includes(s.title)))
}

export function ownerId(email: string): string {
  return sql(`SELECT id FROM users WHERE email = '${email.replaceAll("'", "''")}'`)
}

export function connectionId(owner: string, provider: string): string {
  return sql(`SELECT id FROM connections WHERE owner_id = '${owner}' AND provider = '${provider}'`)
}

/** Spotify on CLIENT_A with the working-playlist consent, then SwedeBeat (which turns on "I use
 *  Swedebeat"). MOWL stays unconnected. */
export async function readyForStudio(page: Page): Promise<void> {
  await playSpotify(page)
  await connectSpotify(page, CLIENT_A)
  await expect(page.getByText('Spotify is connected.')).toBeVisible()
  await page.getByRole('switch', { name: 'Allow the working playlist' }).click()
  await expect(page.getByTestId('spotify-consent-state')).toHaveText('You have allowed the working playlist.')
  await spotifyDialog(page).getByRole('button', { name: 'Close' }).click()
  await expect(spotifyDialog(page)).toHaveCount(0)
  const dialog = await openSwedebeatDialog(page)
  await fillSwedebeat(dialog, EMAIL_A, PASSWORD_A)
  await dialog.getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(page.getByRole('dialog', { name: 'Connect Swedebeat' })).toHaveCount(0)
}

export async function csrf(page: Page): Promise<Record<string, string>> {
  const session = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
  return { 'X-CSRF-Token': session.csrf_token, Origin: new URL(page.url()).origin }
}

/** Describe a studio class: a `studio_songs` pick on the recorded `e2e_pick`. Returns its job id. */
export async function startStudioPick(
  page: Page,
  owner: string,
  title: string,
  script: ModelScript = 'e2e_pick',
): Promise<string> {
  useModelScript(script)
  const answer = await page.request.post('/api/jobs', {
    data: {
      kind: 'studio_songs',
      input: {
        title,
        brief: STUDIO_BRIEF,
        spotify_connection_id: connectionId(owner, 'spotify'),
        swedebeat_connection_id: connectionId(owner, 'swedebeat'),
      },
    },
    headers: { ...(await csrf(page)), 'Idempotency-Key': `studio-${String(Date.now())}-${String(Math.random()).slice(2, 10)}` },
  })
  expect(answer.status(), await answer.text()).toBe(201)
  return ((await answer.json()) as { id: string }).id
}

export async function untilJob(page: Page, id: string, state: string, timeout = 120_000): Promise<void> {
  await expect.poll(async () => (await getJob(page, id)).state, { timeout }).toBe(state)
}

const MAP = [
  'import json',
  'from mowl import track',
  // backend/tests/studiomaps.track_map(240): what the backend design tests stand the analysis in by.
  'd, bpm, fd = 240.0, 128.0, 0.4',
  'phrase = 16 * 240.0 / bpm',
  'events = [{"t": round(fd + phrase * k, 3), "kind": "drop", "song": 0} for k in range(1, int(d / phrase))]',
  'm = {"duration": d, "songs": [{"start": 0, "end": d, "bpm": bpm, "first_downbeat": fd, "first_phrase": fd,',
  '      "phrase_bars": 8, "grid_score": 0.9, "beat1_margin": 0.9}], "events": events,',
  '     "sections": [{"start": 0.0, "end": d / 2, "energy": 0.0}, {"start": d / 2, "end": d, "energy": 1.0}],',
  '     "track_version": track.TRACK_VERSION}',
  'print(json.dumps({"version": track.TRACK_VERSION, "map": m}))',
].join('\n')

const AUDIO = [
  'import urllib.request as u',
  'u.install_opener(u.build_opener(u.ProxyHandler({})))',
  'print(u.urlopen("https://gateway.classbeat-e2e.test/__e2e/audio", timeout=15).read().decode())',
].join('\n')

/**
 * The approved list's analyses, seeded in the user's track cache as the backend's design tests
 * stand the sandboxed analysis in (a 240 s map at 128 BPM per track) with the SHA-256 of the fake
 * gateway's download, so the recorded design fits them and the mix renders from the real
 * downloads. Nothing is analysed on the stack.
 */
export function seedTrackMaps(owner: string, listRevisionId: string): void {
  const references = sql(
    `SELECT s ->> 'reference' FROM class_revisions r, jsonb_array_elements(r.content -> 'songs') e, ` +
      `LATERAL (SELECT e -> 'swedebeat' AS s) x WHERE r.id = '${listRevisionId}' AND s IS NOT NULL`,
  )
    .split('\n')
    .filter((r) => /^[A-Za-z0-9_-]{1,64}$/.test(r))
  expect(references.length).toBeGreaterThan(0)
  const made = JSON.parse(inContainer('worker', MAP)) as { version: number; map: unknown }
  // The SHA-256 of what the fake gateway serves for every track (240 s of generated silence,
  // compose.e2e.yaml): the render checks each download against it.
  const { sha256 } = JSON.parse(inContainer('web', AUDIO)) as { sha256: string }
  const map = JSON.stringify(made.map).replaceAll("'", "''")
  const provenance = seedProvenance('studio', sha256)
  for (const reference of references) {
    sql(
      `INSERT INTO swedebeat_track_maps (owner_id, reference, analyser_version, sha256, map, provenance) VALUES ` +
        `('${owner}', '${reference}', ${String(made.version)}, '\\x${sha256}'::bytea, '${map}'::jsonb, '${provenance}'::jsonb) ` +
        `ON CONFLICT (owner_id, reference, analyser_version) DO UPDATE SET map = EXCLUDED.map, ` +
        `sha256 = EXCLUDED.sha256, provenance = EXCLUDED.provenance, created_at = now()`,
    )
  }
}

/** The row of one Spotify sync state on the page (ADR 0036 section 9). */
export const syncState = (page: Page, target: 'working_copy' | 'final') => page.getByTestId(`studio-sync-${target}-state`)
