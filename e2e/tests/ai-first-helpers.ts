import { expect, type Page } from '@playwright/test'
import { signedInWithMowl } from './class-helpers'
import { useModelScript, type ModelScript } from './model-script'
import { sql } from './song-helpers'

// Shared by the ai-first*.spec.ts files (MA5, ADR 0029, plan "Verification"): the free-text start
// screen, the hand-off to the design and the workspace, against the fake model, MOWL and Spotify.

export const LONG = { timeout: 90_000 }

/** A signed-in user with MOWL connected on the instructor plan (Individual: 75 minutes, games and FTP
 *  tests allowed). The free plan has no games, no FTP tests and 30 minutes, so the suite's requests
 *  need the plan above it. */
export async function instructorWithMowl(page: Page, tag: string): Promise<void> {
  await signedInWithMowl(page, tag)
  const me = (await (await page.request.get('/api/auth/session')).json()) as { user?: { id?: string } }
  const id = me.user?.id
  if (id === undefined || !/^[0-9a-f-]{36}$/.test(id)) throw new Error('no signed-in user id')
  sql(`INSERT INTO entitlements (owner_id, plan_id, source) VALUES ('${id}', 'instructor', 'grant')`)
}

export interface Held {
  release: () => void
}

/**
 * Holds the browser's `design-from-interpretation` call until `release()`. The worker reads the
 * model script anew for every run, so a spec points the script at the design between the reading
 * and the hand-off: the reading is shown first (the hand-off asks only after it), and the call
 * waits here while the spec switches the script.
 */
export async function holdHandoff(page: Page): Promise<Held> {
  let open: () => void = () => undefined
  const gate = new Promise<void>((resolve) => {
    open = resolve
  })
  await page.route('**/api/classes/*/design-from-interpretation', async (route) => {
    await gate
    await route.continue()
  })
  return { release: open }
}

/** On the start screen, say the request in the box (the music stays "classbeat chooses"). */
export async function say(page: Page, request: string): Promise<void> {
  await page.goto('/classes/new')
  await page.getByLabel('Describe your class').fill(request)
}

/** Choose a row of the Music picker by its title. */
export async function pickMusic(page: Page, title: string): Promise<void> {
  await page.getByRole('button', { name: /^Music/ }).click()
  await page.getByRole('radio', { name: new RegExp(`^${title}`) }).click()
}

/**
 * Start a class from words: read with `reading`, then design with `design`. Returns once the
 * workspace shows the finished class. `afterReading` runs between the two, with the hand-off held.
 */
export async function designFromWords(
  page: Page,
  request: string,
  scripts: { reading: ModelScript; design: ModelScript },
  options: { music?: string; afterReading?: () => Promise<void> | void } = {},
): Promise<string> {
  useModelScript(scripts.reading)
  const held = await holdHandoff(page)
  await say(page, request)
  if (options.music !== undefined) await pickMusic(page, options.music)
  await page.getByRole('button', { name: 'Design my class' }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  await expect(page.getByTestId('interpretation')).toBeVisible(LONG)
  await options.afterReading?.()
  useModelScript(scripts.design)
  held.release()
  return page.url()
}
