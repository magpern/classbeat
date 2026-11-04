import { expect, type Locator, type Page } from '@playwright/test'
import { signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'
import { connectMowl, choosePlaylist, fakeControl, getJob, jobId, startDesign } from './design-helpers'
import { useModelScript, type ModelScript } from './model-script'
import { sql } from './song-helpers'

// Shared by class-save.spec.ts and class-usage.spec.ts (M3b T2, ADR 0007 section 13): sign up,
// design a class, change it by chat, and read or steer the fake MOWL's programs.

export const LONG = { timeout: 60_000 }

/** A program as the fake MOWL holds it (no tokens): see `save_programs` in e2e/fake-mowl. */
export interface FakeProgram {
  program_id: number
  /** 0 for the account that owns the oldest program, 1 for the next, ... */
  account: number
  name: string
  description: string
  public: boolean
  playlist_id: number
  segments: string[]
  /** Every interval's [duration, zone]. */
  intervals: number[][]
  /** The program's own sport as written: 1 running, 0 cycling (ADR 0018); absent on old programs. */
  activity_type?: number | null
  /** Each segment's own `ActivityTypeID` (0 for every segment of a running program). */
  segment_activity_types?: (number | null)[]
  /** Every interval's incline (`Gradient`): 0 flat, 1 incline for a running program. */
  gradients?: (number | null)[]
}

/** The zone of every interval of a program, in order. */
export const zonesOf = (program: FakeProgram): number[] => program.intervals.map((i) => i[1]!)

export interface SaveState {
  writes: number
  fail: Record<string, string>
  /** Programs per account. */
  programs: number[]
}

/** Forget every program, zero the write counter and inject the failures: `fail` is the fake's
 *  `<n>:<refuse|drop|noid|copy>,...` (the n-th write of the next save or update). */
export function saveArm(fail = '', reset = false): SaveState {
  const query = [fail === '' ? '' : `fail=${fail}`, reset ? 'reset=1' : ''].filter(Boolean).join('&')
  return fakeControl('POST', `save-arm${query === '' ? '' : `?${query}`}`)
}

export const saveState = (): SaveState => fakeControl('GET', 'save-state')

export const savePrograms = (): FakeProgram[] =>
  fakeControl<{ programs: FakeProgram[] }>('GET', 'save-programs').programs

/** Hide or show the programs in the fake's My Workouts listing, as if MOWL had not caught up with a
 *  write (ADR 0007 section 8: absence is not proof). `saveArm` shows them again. */
export const listing = (hidden: boolean): void => {
  fakeControl('POST', `save-listing?hidden=${hidden ? 1 : 0}`)
}

/** The fake MOWL's `save-edit`: the user renames the program in the MOWL app. */
export const editInMowl = (programId: number): void => {
  fakeControl('POST', `save-edit?program=${programId}`)
}

/** The playlist's MOWL id in the fake (e2e/fake-mowl PLAYLISTS), which a program carries. */
export const DRAFT_PLAYLIST_MOWL_ID = 7003

/** The writes of one save or update of the 7-segment recorded draft: the program, three per
 *  segment, and the segment list. The last one completes the workout. */
export const LAST_WRITE = 1 + 3 * 7 + 1

export async function signedInWithMowl(page: Page, tag: string): Promise<void> {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email) // a fresh sign-in counts as recent authentication
  await connectMowl(page)
}

/** Design the recorded class from the playlist and open its class page. */
export async function designAndOpen(page: Page, title: string, playlistName: string): Promise<string> {
  useModelScript('good_first_draft')
  const id = await startDesign(page, title, playlistName)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  expect((await getJob(page, id)).state).toBe('completed')
  await page.goto('/classes')
  await page.getByRole('link', { name: title }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  return id
}

/** Open the new-class form and choose Running (the control exists only while the flag is on), then
 *  fill it for the recorded running script (30 minutes, one playlist) and start the design. */
export async function startRunningDesign(page: Page, title: string, playlistName: string): Promise<string> {
  await page.getByRole('link', { name: 'New class' }).click()
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByRole('button', { name: 'Running', exact: true }).click()
  await form.getByLabel('Class name').fill(title)
  await choosePlaylist(form, playlistName)
  await form.getByLabel('Length in minutes').fill('30')
  await form.getByRole('button', { name: 'Design my class' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  return jobId(page)
}

/**
 * The stored admin flag `running_enabled` (ADR 0018 section 5, #509): only a stored `on` switches
 * running on under the stack's environment switch; nothing stored is off. The web runs the `fake`
 * provider outside prod in this stack, which counts as covering every plan, so `on` is enough.
 */
export function storeRunning(value: 'on' | 'off' | null): void {
  if (value === null) {
    sql("DELETE FROM app_settings WHERE key = 'running_enabled'")
    return
  }
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('running_enabled', '"${value}"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = '"${value}"'`,
  )
}

/** Design the recorded running class from the playlist and open its class page. */
export async function designRunningAndOpen(page: Page, title: string, playlistName: string): Promise<string> {
  useModelScript('running_first_draft')
  const id = await startRunningDesign(page, title, playlistName)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  expect((await getJob(page, id)).state).toBe('completed')
  await page.goto('/classes')
  await page.getByRole('link', { name: title }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  return id
}

/** The fake MOWL's `save-sport`: the program in MOWL is now another sport's (0 cycling, 1 running). */
export const setProgramSport = (programId: number, sport: 0 | 1): void => {
  fakeControl('POST', `save-sport?program=${String(programId)}&sport=${String(sport)}`)
}

export function classId(page: Page): string {
  const id = /\/classes\/([0-9a-f-]{36})$/.exec(page.url())?.[1]
  if (id === undefined) throw new Error(`not on a class page: ${page.url()}`)
  return id
}

/** Ask for a change in the chat; the model replays `script`. Waits for the new version. */
export async function reviseByChat(page: Page, script: ModelScript, request: string, version: number): Promise<void> {
  useModelScript(script)
  await page.getByLabel('What should change?').fill(request)
  await page.getByRole('button', { name: 'Change it', exact: true }).click()
  await expect(page.getByText(`classbeat changed it, version ${version}`)).toBeVisible(LONG)
}

export const savePanel = (page: Page): Locator => page.getByTestId('save-panel')
export const savedState = (page: Page): Locator => page.getByTestId('saved-state')

/** Click a button of the Save panel, then the confirmation's button (the writes are asked first). */
export async function confirmWrite(page: Page, button: string | RegExp, confirm: string): Promise<void> {
  await savePanel(page).getByRole('button', { name: button }).click()
  const group = savePanel(page).getByRole('group', { name: 'Confirm' })
  await expect(group).toBeVisible()
  await group.getByRole('button', { name: confirm, exact: true }).click()
}

/** The undecided writes' panel of the class page. */
export const undecided = (page: Page): Locator => page.getByTestId('undecided')
