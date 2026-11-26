import { expect, type Page } from '@playwright/test'
import { signedInWithMowl } from './class-helpers'
import { useModelScript, type ModelScript } from './model-script'
import { sql } from './song-helpers'
import { withSpotify } from './spotify-helpers'

// Kept here for the specs that import it from this file.
export { withSpotify }

// Shared by the ai-first*.spec.ts files (MA5, ADR 0029, plan "Verification"): the free-text start
// screen, the hand-off to the design and the workspace, against the fake model, MOWL and Spotify.

export const LONG = { timeout: 90_000 }

/** A signed-in user with MOWL and Spotify connected on the instructor plan (Individual: 75 minutes, games and FTP
 *  tests allowed). The free plan has no games, no FTP tests and 30 minutes, so the suite's requests
 *  need the plan above it. */
export async function instructorWithMowl(page: Page, tag: string): Promise<void> {
  await signedInWithMowl(page, tag)
  const me = (await (await page.request.get('/api/auth/session')).json()) as { user?: { id?: string } }
  const id = me.user?.id
  if (id === undefined || !/^[0-9a-f-]{36}$/.test(id)) throw new Error('no signed-in user id')
  sql(`INSERT INTO entitlements (owner_id, plan_id, source) VALUES ('${id}', 'instructor', 'grant')`)
  await withSpotify(page)
}

export interface Held {
  /** Agrees to the reading: clicks "Design this class". */
  release: () => Promise<void>
}

/**
 * The page waits for the instructor after "How we read your request" (#1538): nothing is designed or
 * fetched until "Design this class" is clicked. `release()` clicks it, so a spec reads as before: it
 * points the model script at the design between the reading and the hand-off, then releases. A reading
 * with a question to answer first is answered by the spec itself.
 */
export function holdHandoff(page: Page): Held {
  return {
    release: async () => {
      await page.getByRole('button', { name: 'Design this class' }).click()
    },
  }
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
 * `withinPlan`: the request is over the plan, so the page asks first and this chooses "Design within my plan".
 */
export async function designFromWords(
  page: Page,
  request: string,
  scripts: { reading: ModelScript; design: ModelScript },
  options: { music?: string; afterReading?: () => Promise<void> | void; withinPlan?: boolean } = {},
): Promise<string> {
  useModelScript(scripts.reading)
  const held = holdHandoff(page)
  await say(page, request)
  if (options.music !== undefined) await pickMusic(page, options.music)
  await page.getByRole('button', { name: 'Design my class' }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  await expect(page.getByTestId('interpretation')).toBeVisible(LONG)
  await options.afterReading?.()
  useModelScript(scripts.design)
  await held.release()
  // A request over the plan waits for a choice before the design job starts (M7a-4).
  if (options.withinPlan === true) await page.getByRole('button', { name: 'Design within my plan' }).click()
  return page.url()
}
