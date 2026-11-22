import { type Page } from '@playwright/test'
import { expect, test } from './helpers'
import { jobId } from './design-helpers'
import { useModelScript } from './model-script'
import { sql } from './song-helpers'
import { resetSpotify } from './spotify-helpers'
import { resetSpotdown } from './spotdown-helpers'
import { LONG } from './studio-flow-helpers'
import { csrf, syncState, untilJob } from './studio-helpers'
import { gatewayRequests, resetSwedebeat } from './swedebeat-helpers'

// Shared by the DJ mix specs (ADR 0036 amendment B, #1374): the setup of the fakes, the screen's
// apply and approve steps, and the reads the specs compare with what the fakes recorded: the
// preview's windows, the approved songs' references, the stored render plan and the gateway's
// recorded playlist writes. The writes are never made here: the page and the worker make them.

/** The fakes' setup of every spec here: song picking on, and the three fakes forgotten around each test. */
export function useStudioFakes(): void {
  test.beforeAll(() => {
    sql(
      `INSERT INTO app_settings (key, value) VALUES ('song_picking', '"on"') ` +
        `ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
    )
  })
  const reset = () => {
    resetSpotify()
    resetSwedebeat()
    resetSpotdown()
  }
  test.beforeEach(reset)
  test.afterAll(() => {
    reset()
    sql(`DELETE FROM app_settings WHERE key = 'song_picking'`)
  })
}

/** No job of the user is open: the next test resets the fakes, so nothing may still be writing to them. */
export async function untilIdle(owner: string): Promise<void> {
  const open = () =>
    sql(`SELECT count(*) FROM jobs WHERE owner_id = '${owner}' AND state IN ('queued', 'running', 'waiting_for_approval')`)
  await expect.poll(open, LONG).toBe('0')
}

/** Apply these choices: the new draft is made by a job with no model; back on the class page after it. */
export async function applyChoices(page: Page, classId: string): Promise<void> {
  await page.getByTestId('studio-outputs').getByRole('button', { name: 'Apply these choices' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/, LONG)
  await untilJob(page, jobId(page), 'completed')
  await page.goto(`/classes/${classId}`)
}

/** Approve and create the chosen outputs, wait for the outputs job and for the final Spotify playlist. */
export async function approveAndCreate(page: Page, classId: string, owner: string): Promise<void> {
  const approve = page.getByTestId('studio-outputs').getByRole('button', { name: 'Approve and create' })
  await expect(approve).toBeEnabled(LONG)
  await approve.click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/, LONG)
  await untilJob(page, jobId(page), 'completed', 240_000)
  await page.goto(`/classes/${classId}`)
  await expect(syncState(page, 'final')).toHaveText('up to date', LONG)
  await untilIdle(owner)
}

/** The edit loop's chat revise (`e2e_studio_revise`): the last song is cut to 230 s. */
export async function cutLastSong(page: Page, classId: string): Promise<void> {
  useModelScript('e2e_studio_revise')
  await page.goto(`/classes/${classId}`)
  await expect(page.getByRole('heading', { name: 'Music timeline' })).toBeVisible(LONG)
  await page.getByLabel('What should change?').fill('Cut the last song shorter')
  await page.getByRole('button', { name: 'Change it', exact: true }).click()
  await expect(page.getByText(/Changed, version \d+/)).toBeVisible(LONG)
  await expect(page.getByRole('heading', { name: 'Music timeline' })).toBeVisible(LONG)
}

// What the API says -------------------------------------------------------------------------------

export interface MixChoices {
  dj_mix: boolean
  blend: boolean
  trim: boolean
  keep_whole: number[]
  hard_cut_in: number[]
  hard_cut_out: number[]
}

export const DJ_DEFAULT: MixChoices = { dj_mix: true, blend: true, trim: true, keep_whole: [], hard_cut_in: [], hard_cut_out: [] }

interface Window {
  position: number
  start_sec: number
  end_sec: number
}

export interface MixPreview {
  refusal: { reason: string } | null
  windows: Window[] | null
  crossfades: number[] | null
  total_sec: number | null
  suggestions: { position: number; length_sec: number; pieces: { start_sec: number; end_sec: number }[] }[]
}

/** The class's current draft, as the timeline names it, and whether it is the plain whole-songs form. */
async function currentDraft(page: Page, classId: string): Promise<{ revision_id: string; content_hash: string; is_plain: boolean }> {
  const answer = await page.request.get(`/api/classes/${classId}/timeline`)
  expect(answer.status(), await answer.text()).toBe(200)
  return (await answer.json()) as { revision_id: string; content_hash: string; is_plain: boolean }
}

/** Whether the class's current draft is the plain whole-songs form (every song whole, no crossfade). */
export async function isPlain(page: Page, classId: string): Promise<boolean> {
  return (await currentDraft(page, classId)).is_plain
}

/** The draft's identity, as the approval and the preview name it. */
async function draftOf(page: Page, classId: string): Promise<{ revision_id: string; content_hash: string }> {
  const { revision_id, content_hash } = await currentDraft(page, classId)
  return { revision_id, content_hash }
}

/** The read-only preview of these choices on the current draft (the screen's own call). */
export async function previewOf(page: Page, classId: string, choices: MixChoices): Promise<MixPreview> {
  const answer = await page.request.post(`/api/classes/${classId}/mix-choices/preview`, {
    data: { ...(await draftOf(page, classId)), choices },
    headers: await csrf(page),
  })
  expect(answer.status(), await answer.text()).toBe(200)
  return (await answer.json()) as MixPreview
}

/** The approval of the current draft for `outputs`, sent straight to the API: what a forged request does. */
export async function forgeOutputs(page: Page, classId: string, outputs: string[]): Promise<{ status: number; reason: string | null }> {
  const answer = await page.request.post(`/api/classes/${classId}/outputs`, {
    data: { ...(await draftOf(page, classId)), outputs },
    headers: { ...(await csrf(page)), 'Idempotency-Key': `forged-${String(Date.now())}-${String(Math.random()).slice(2, 10)}` },
  })
  const body = (await answer.json()) as { detail?: { reason?: string } }
  return { status: answer.status(), reason: body.detail?.reason ?? null }
}

/** The SwedeBeat references of the approved songs, by position. */
export function approvedReferences(owner: string, classId: string): string[] {
  return sql(
    `SELECT e -> 'swedebeat' ->> 'reference' FROM class_revisions r, jsonb_array_elements(r.content -> 'songs') ` +
      `WITH ORDINALITY AS x(e, n) WHERE r.owner_id = '${owner}' AND r.class_id = '${classId}' AND r.kind = 'song_list' ORDER BY n`,
  ).split('\n')
}

export interface PlanEntry {
  reference: string
  src_start: number
  src_end: number
  dst_start: number
  fade_in: number
  fade_out: number
  curve?: string
}

/** The render plan the mix output stored before it wrote anything (`class_outputs.detail`). */
export function mixPlan(owner: string): PlanEntry[] {
  return JSON.parse(
    sql(`SELECT detail -> 'plan' FROM class_outputs WHERE owner_id = '${owner}' AND output = 'swedebeat_mix' ORDER BY attempt DESC LIMIT 1`),
  ) as PlanEntry[]
}

// What the gateway recorded ------------------------------------------------------------------------

export interface PlaylistTrack {
  reference: string
  start_sec: number
  end_sec: number
  trim: boolean | null
  crossfade_sec: number
}

/** The bodies of the `POST /playlists` the fake gateway received. */
export const playlistBodies = (): { crossfade_sec: number; tracks: PlaylistTrack[] }[] =>
  gatewayRequests()
    .filter((r) => r.method === 'POST' && r.path === '/playlists')
    .map((r) => r.body as { crossfade_sec: number; tracks: PlaylistTrack[] })

/** Every playlist and mix write the fake gateway received (create, publish, merge-publish). */
export const gatewayWrites = () => gatewayRequests().filter((r) => r.method !== 'GET' && r.path.startsWith('/playlists'))

export const mergePublishes = () => gatewayWrites().filter((r) => r.path === '/playlists/merge-publish')
