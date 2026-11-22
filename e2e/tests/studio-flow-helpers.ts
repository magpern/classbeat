import { type Page } from '@playwright/test'
import { expect, signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'
import { getJob } from './design-helpers'
import { useModelScript } from './model-script'
import { sql } from './song-helpers'
import { catalogueWithout, ownerId, readyForStudio, startStudioPick, untilJob } from './studio-helpers'

// Shared by the studio specs that run the design on spotdown's maps (ADR 0040 amendment "spotdown
// is the only analysis path", #1363): a studio user on the instructor plan and the studio class
// driven from its description to the timeline with **no seeded analyses**, so the worker analyses
// every track through the fake spotdown. Public test values only.

export const LONG = { timeout: 90_000 }

/** A signed-up user with Spotify, SwedeBeat and no MOWL, on the instructor plan (the studio list
 *  is whole songs within the plan's length, ADR 0036 B6a). Returns the owner id. */
export async function newStudioUser(page: Page, tag: string): Promise<string> {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await readyForStudio(page)
  const owner = ownerId(email)
  sql(`INSERT INTO entitlements (owner_id, plan_id, source) VALUES ('${owner}', 'instructor', 'grant')`)
  return owner
}

/** The pick (on the fake gateway's match), Approve songs and the design on spotdown's maps: the
 *  analyses are not seeded. Returns the class id once the design job has completed. */
export async function designOnSpotdown(page: Page, owner: string, title: string): Promise<string> {
  catalogueWithout()
  const pickId = await startStudioPick(page, owner, title)
  await untilJob(page, pickId, 'waiting_for_approval')
  await page.goto(`/jobs/${pickId}`)
  const list = page.getByRole('region', { name: 'Songs from SwedeBeat' })
  await expect(list).toBeVisible(LONG)
  await expect(list.getByRole('listitem')).toHaveCount(8)
  const pick = await getJob(page, pickId)
  useModelScript('e2e_studio_design')
  await list.getByRole('button', { name: 'Approve songs' }).click()
  const designJob = () =>
    sql(`SELECT id FROM jobs WHERE owner_id = '${owner}' AND class_id = '${pick.class_id}' AND kind = 'studio_design'`)
  await expect.poll(designJob, LONG).toMatch(/^[0-9a-f-]{36}$/)
  await untilJob(page, designJob(), 'completed', 240_000)
  return pick.class_id
}

/** The analysers of the user's stored track maps, one per distinct analyser: `spotdown` only. */
export function mapAnalysers(owner: string): string {
  return sql(`SELECT string_agg(DISTINCT analyser, ',') FROM swedebeat_track_maps WHERE owner_id = '${owner}'`)
}

/** How many track maps the user has stored. */
export function mapCount(owner: string): number {
  return Number(sql(`SELECT count(*) FROM swedebeat_track_maps WHERE owner_id = '${owner}'`))
}

/** How many distinct audio files (sha256) the user's stored maps were made from. The fake gateway
 * serves the same bytes for every track, so this can be less than `mapCount`: a track whose bytes
 * the user already has a map for reuses it without asking spotdown again. */
export function distinctAudio(owner: string): number {
  return Number(sql(`SELECT count(DISTINCT sha256) FROM swedebeat_track_maps WHERE owner_id = '${owner}'`))
}

/** The outputs of a class as `output:state`, sorted. */
export function outputStates(owner: string): string {
  return sql(`SELECT string_agg(output || ':' || state, ',' ORDER BY output) FROM class_outputs WHERE owner_id = '${owner}'`)
}
