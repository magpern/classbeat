import { expect, type Locator, type Page } from '@playwright/test'
import { signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'
import { connectMowl, fakeControl, getJob, jobId, startDesign } from './design-helpers'
import { useModelScript, type ModelScript } from './model-script'

// Shared by class-save.spec.ts and class-usage.spec.ts (M3b T2, ADR 0007 section 13): sign up,
// design a class, change it by chat, and read or steer the fake MOWL's programs.

export const LONG = { timeout: 60_000 }

/** A program as the fake MOWL holds it (no tokens): see `save_programs` in e2e/fake-mowl. */
export interface FakeProgram {
  program_id: number
  /** 0 for the account that owns the oldest program, 1 for the next, ... */
  account: number
  name: string
  public: boolean
  playlist_id: number
  segments: string[]
  /** Every interval's [duration, zone]. */
  intervals: number[][]
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
