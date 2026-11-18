import { existsSync, readdirSync, readFileSync } from 'node:fs'
import type { Page } from '@playwright/test'
import { expect, test } from './helpers'
import { PLAYLISTS, fakeMowl } from './design-helpers'
import { saveArm, saveState, classId } from './class-helpers'
import { LONG, instructorWithMowl } from './ai-first-helpers'
import { useModelScript, type ModelScript } from './model-script'
import { sql } from './song-helpers'
import { fakeState } from './spotify-helpers'

// ADR 0038 section 10.2 and issue #1191: the composer class workspace end to end on the fakes
// with the flag on. One attached Spotify playlist and one pinned song are described; a question is
// answered, the shape is changed, a song is swapped, and the class is approved and saved. Exactly
// one MOWL save and one final Spotify playlist may result, and nothing is written before Approve.
//
// The reading, sketch and first change scripts are in `backend/tests/agent_scripts`
// (`composer_read`, `composer_sketch`, `composer_sketch_revised`, pinned by
// `backend/tests/test_composer_scripts.py`). The order of the script switches against the fill that
// starts by itself after the sketch is finished when the routes of #1188 and #1189 are there.
//
// It needs what #1188 (the fill hand-off, swap and change) and #1189 (approve with `save: true`,
// `save_pending`) add: the `fill-songs` route, the fake model scripts named in `SCRIPTS` and the
// class answer's `save_pending`. Until all exist the spec skips itself with that reason (a probe,
// not a flag): the assertions below are the ones the issue asks for and are not weakened. #1188 and
// #1189 are on main; the spec still skips until the #1191 follow-up adapts the workspace (it
// switches Create the playlist again to `resume-songs`, the last probe).

const SCRIPTS: ModelScript[] = ['composer_read', 'composer_sketch', 'composer_sketch_revised', 'composer_fill', 'composer_swap']
const NO_CLASS = '00000000-0000-4000-8000-000000000000'
const PINNED = 'Dancing Queen'

/** The stored admin flag `composer_flow` (ADR 0038 section 2): only a stored `on` switches it on under the switch. */
function storeComposer(value: 'on' | null): void {
  if (value === null) {
    sql("DELETE FROM app_settings WHERE key = 'composer_flow'")
    return
  }
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('composer_flow', '"${value}"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = '"${value}"'`,
  )
}

/**
 * Whether the class answer carries `save_pending` (a field of a model in `backend/src/classbeat/classes`),
 * which only #1189 adds: without it the approval cannot finish. Remove with the skip when #1189 is on main.
 */
function classAnswerHasSavePending(): boolean {
  const dir = new URL('../../backend/src/classbeat/classes/', import.meta.url)
  return readdirSync(dir)
    .filter((name) => name.endsWith('.py'))
    .some((name) => /\bsave_pending\s*:/.test(readFileSync(new URL(name, dir), 'utf8')))
}

/**
 * Whether the web's Create the playlist again uses the composer's resume hand-off
 * (`POST /api/classes/{id}/resume-songs`, #1189), which only the #1191 follow-up wires in
 * (`frontend/src/api/resumePlaylist.ts`): until then the workspace and its e2e flow are not adapted
 * to the real backend shapes. Remove with the skip in that PR.
 */
function webUsesTheResumeHandOff(): boolean {
  const file = new URL('../../frontend/src/api/resumePlaylist.ts', import.meta.url)
  return existsSync(file) && readFileSync(file, 'utf8').includes('/resume-songs')
}

/** Why the spec cannot run yet, or null. A missing route is FastAPI's own `{"detail":"Not Found"}`; the app's answers carry a reason. */
async function missing(page: Page): Promise<string | null> {
  const absent = SCRIPTS.filter(
    (name) => !existsSync(new URL(`../../backend/tests/agent_scripts/${name}.json`, import.meta.url)),
  )
  if (absent.length > 0) return `the fake model scripts ${absent.join(', ')} are not in backend/tests/agent_scripts yet (#1188, #1189)`
  if (!classAnswerHasSavePending()) {
    return 'the approval\'s phase 2 (approve and save, `save_pending` on the class answer) is not on main yet (#1189)'
  }
  if (!webUsesTheResumeHandOff()) {
    return 'the workspace is not adapted to the approve-and-save backend yet: it waits for the #1191 e2e follow-up'
  }
  const session = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
  const probe = await page.request.post(`/api/classes/${NO_CLASS}/fill-songs`, {
    data: {},
    headers: { 'X-CSRF-Token': session.csrf_token },
  })
  const body = (await probe.json().catch(() => ({}))) as { detail?: unknown }
  return probe.status() === 404 && body.detail === 'Not Found'
    ? 'POST /api/classes/{id}/fill-songs does not exist yet (#1188)'
    : null
}

/** Every write the fake Spotify has seen to a playlist (a create, an add, a replace, a change). */
const spotifyWrites = () =>
  fakeState().requests.filter((r) => r.method !== 'GET' && /\/playlists/.test(r.path) && !/\/(api\/)?token/.test(r.path))

test.beforeEach(() => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true) // no programs, no injected failures
  storeComposer('on')
})

test.afterEach(() => {
  storeComposer(null)
})

test('describe with a playlist and a pinned song, shape it, swap a song, approve and save: one MOWL save, one Spotify playlist, no write before Approve', async ({
  page,
}) => {
  test.setTimeout(360_000)
  await instructorWithMowl(page, 'composer')
  const reason = await missing(page)
  test.skip(reason !== null, reason ?? '')

  // Describe: the text and one attached playlist.
  useModelScript('composer_read')
  await page.goto('/classes/new')
  await page
    .getByLabel('Describe your class')
    .fill(`A hard climbing ride. Start with ${PINNED} by ABBA.`)
  await page.getByRole('button', { name: /^Add music/ }).click()
  await page.getByRole('button', { name: new RegExp(PLAYLISTS.draft.name) }).click()
  useModelScript('composer_sketch')
  await page.getByRole('button', { name: 'Start' }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  const id = classId(page)

  // Shape it together: the reading with its pinned song and question, and the rough sketch.
  const reading = page.getByTestId('reading-card')
  await expect(reading.getByText(PINNED)).toBeVisible(LONG)
  await expect(page.getByText('Rough: songs not in yet')).toBeVisible(LONG)

  // Nothing is written anywhere before the approval: no MOWL program, no Spotify playlist write.
  const beforeApproval = () => {
    expect(saveState().writes).toBe(0)
    expect(spotifyWrites()).toHaveLength(0)
  }
  beforeApproval()

  // Answer the question with a quick answer: a prepared sentence sent as a change.
  useModelScript('composer_sketch_revised')
  await reading.getByRole('button', { name: '60 minutes' }).click()
  await expect(page.getByText('Make the class 60 minutes long.')).toBeVisible(LONG)

  // Change the shape in the chat (the second revise: the script chains from the first).
  useModelScript('soundless_revise_second')
  await page.getByLabel('Tell classbeat what to change').fill('Make the last climb longer.')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText('Make the last climb longer.')).toBeVisible(LONG)

  // The songs come in by themselves: the class is ready to approve, with the pinned song marked.
  useModelScript('composer_fill')
  await expect(page.getByText('Ready to approve')).toBeVisible(LONG)
  await expect(page.getByTestId('segment-list').getByText('Pinned by you')).toBeVisible()
  beforeApproval()

  // Swap one song: the waiting version is replaced, still not written anywhere.
  useModelScript('composer_swap')
  await page.getByRole('button', { name: /^Swap / }).first().click()
  await expect(page.getByText('Ready to approve')).toBeVisible(LONG)
  beforeApproval()

  // Approve and save: the bar's copy, then one click.
  await expect(
    page.getByText(
      'Approving saves a private workout to MOWL and creates a playlist in your Spotify in this order (we ask Spotify for private).',
    ),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Approve and save' }).click()
  await expect(page.getByText('Saved', { exact: true })).toBeVisible(LONG)

  // One save to MOWL (the fake counts its write requests for one program) and one final playlist.
  const programs = saveState().programs
  expect(programs.reduce((sum, n) => sum + n, 0)).toBe(1)
  const created = spotifyWrites().filter((r) => r.method === 'POST' && /\/users\/[^/]+\/playlists$/.test(r.path))
  expect(created).toHaveLength(1)
  // The class holds a draft that is saved: no Save button, only Update when it changes.
  await expect(page.getByRole('button', { name: 'Save to MOWL' })).toHaveCount(0)
  expect(id).toMatch(/^[0-9a-f-]{36}$/)
})
