import { expect, type Page } from '@playwright/test'
import { connectMowl, getJob, jobId } from './design-helpers'
import { useModelScript, type ModelScript } from './model-script'
import { CLIENT_A, compose, connectSpotify, control, playSpotify } from './spotify-helpers'

// Shared by song-pick.spec.ts: a user with MOWL and Spotify connected and the working-playlist
// consent given, and the new-class form's "Pick songs for me". Public test values only.

/** A signed-in page with MOWL connected, Spotify connected on CLIENT_A and the consent given. */
export async function readyToPick(page: Page, clientId: string = CLIENT_A): Promise<void> {
  await connectMowl(page)
  await playSpotify(page)
  await connectSpotify(page, clientId)
  await expect(page.getByText('Spotify is connected.')).toBeVisible()
  await page.getByRole('switch', { name: 'Allow the working playlist' }).click()
  await expect(page.getByTestId('spotify-consent-state')).toHaveText('You have allowed the working playlist.')
}

/** Fill the new-class form for a song pick (30 minutes, one climb, two sprints) and start it.
 *  Returns the job's id. The model script for phase 1 is chosen first. */
export async function startPick(page: Page, title: string, script: ModelScript = 'e2e_pick'): Promise<string> {
  useModelScript(script)
  await page.getByRole('link', { name: 'New class' }).click()
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByLabel('Class name').fill(title)
  await form.getByRole('button', { name: 'Pick songs for me' }).click()
  await form.getByLabel('Length in minutes').fill('30')
  await form.getByLabel('Climbs', { exact: true }).fill('1')
  await form.getByLabel('Sprints', { exact: true }).fill('2')
  await form.getByRole('button', { name: 'Design my class' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  return jobId(page)
}

/** The job's state as the API shows it, polled until `state` (the worker runs it in the background). */
export async function untilState(page: Page, id: string, state: string): Promise<void> {
  await expect.poll(async () => (await getJob(page, id)).state, { timeout: 90_000 }).toBe(state)
}

/** One SQL statement as the database superuser, in the stack's own db container, for what the tests
 *  cannot wait for (an approval that has passed its date). Constants only: never user text. */
export function sql(statement: string): string {
  return compose(['exec', '-T', 'db', 'psql', '-U', 'postgres', '-d', 'classbeat', '-At', '-c', statement]).trim()
}

export interface FakePlaylist {
  id: string
  owner: string
  name: string
  public: boolean
  collaborative: boolean
  entries: (string | null)[]
}

/** The playlists the fake Spotify holds, with their owner, privacy and track URIs in order. */
export const fakePlaylists = (): FakePlaylist[] => control<{ playlists: FakePlaylist[] }>('GET', 'state').playlists

/** The titles of the songs on the Songs for approval page, in order. */
export async function listedTitles(page: Page): Promise<string[]> {
  await expect(page.getByTestId('song').first()).toBeVisible() // the list may still be loading
  return page.getByTestId('song').locator('p:first-child').allInnerTexts()
}
