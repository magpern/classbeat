import { type Page } from '@playwright/test'
import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { getJob, jobId } from './design-helpers'
import { useModelScript } from './model-script'
import { audit, setTheme, stackContext, type Findings } from './mu-helpers'
import { listedTitles, readyToPick, sql, startPick, untilState } from './song-helpers'
import { resetSpotify } from './spotify-helpers'
import { CACHED_MIX_ID, ownerIdForEmail, resetSwedebeatMixes, seedCachedMix, swedebeatMixState, workerLogTail } from './swedebeat-mix-helpers'
import { EMAIL_A, PASSWORD_A, catalogueCalls, fakeState, resetSwedebeat, setCatalogue, useSwedebeatThroughUi } from './swedebeat-helpers'

// ADR 0016 section 5 to 7, path (b), on the fakes (B5): a picked class is matched against the fake
// Swedebeat catalogue (found, unsure, missing), its two unmatched songs are replaced and matched
// again, the selection is approved, "Next, in the Swedebeat app" is verified with a mix of the wrong
// length (not found first, then the user's own pick, too short), the user overrides the verdict and
// the class is designed on that mix. Every state of the match view is audited at 390 and 1440 px in
// both themes (axe, no sideways scroll, the primary action visible). No real Swedebeat, MOWL or
// Spotify account and no model: the fakes answer, the worker replays recorded scripts.

test.describe.configure({ mode: 'serial' })
test.setTimeout(300_000)
const LONG = { timeout: 90_000 }

const findings: Findings[] = []
let classId = ''
let ownTitles: string[] = []

test.beforeAll(() => {
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('song_picking', '"on"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
  )
})

test.beforeEach(() => {
  resetSpotify()
  resetSwedebeat()
  resetSwedebeatMixes()
})

test.afterAll(() => {
  resetSpotify()
  resetSwedebeat()
  sql(`DELETE FROM app_settings WHERE key = 'song_picking'`)
})

/** Only a Content-Security-Policy violation fails here; a 404 the page expects is not one. */
function watchCsp(page: Page): string[] {
  const seen: string[] = []
  page.on('console', (message) => {
    if (/Content Security Policy|Refused to/.test(message.text())) seen.push(message.text())
  })
  return seen
}

/** Audit the state on screen at both sizes and in both themes, then put the page back as it was. */
async function check(page: Page, name: string, primary: ReturnType<Page['locator']>): Promise<void> {
  // axe looks at the whole class page (the revisions list's names were fixed in #475, and the
  // two-version class is in the axe walk of mu-pages.spec.ts).
  const result = await audit(page, name, primary)
  findings.push(result)
  expect.soft(result.problems, name).toEqual([])
  await setTheme(page, 'dark')
  await page.setViewportSize({ width: 1280, height: 720 })
}

const card = (page: Page) => page.getByRole('region', { name: 'Swedebeat', exact: true })
const match = (page: Page) => page.getByRole('region', { name: 'Swedebeat match' })
const nextStep = (page: Page) => page.getByRole('region', { name: 'Next, in the Swedebeat app' })

async function connectSwedebeat(page: Page): Promise<void> {
  await page.goto('/account')
  await useSwedebeatThroughUi(page)
  await card(page).getByLabel('Swedebeat email').fill(EMAIL_A)
  await card(page).getByLabel('Swedebeat password').fill(PASSWORD_A)
  await card(page).getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(card(page).getByText('Swedebeat is connected.')).toBeVisible()
}

test('match, replace, approve, then verify with a wrong-length mix and override', async ({ page }) => {
  const csp = watchCsp(page)
  const email = uniqueEmail('sb-match')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email) // a fresh sign-in counts as recent authentication
  await connectSwedebeat(page)
  await readyToPick(page)

  await test.step('a picked class, approved once', async () => {
    const id = await startPick(page, 'E2E matched ride')
    await untilState(page, id, 'waiting_for_approval')
    ownTitles = await listedTitles(page)
    expect(ownTitles).toHaveLength(8)
    useModelScript('good_first_draft')
    await page.getByRole('button', { name: 'Approve songs' }).click()
    await untilState(page, id, 'completed')
    const href = await page.getByRole('link', { name: 'Open the class' }).first().getAttribute('href')
    classId = /\/classes\/([0-9a-f-]{36})/.exec(href ?? '')![1]!
    await page.goto(`/classes/${classId}`)
    await expect(card(page)).toBeVisible(LONG)
    // Nothing went to the catalogue before the user asked for the match.
    expect(catalogueCalls()).toEqual([])
  })

  await test.step('the match: found, unsure and missing', async () => {
    // Song 3 is not in the catalogue, song 5 only under another title, the other six are.
    const unsure = ownTitles[4]!
    setCatalogue(ownTitles.filter((_, i) => i !== 2 && i !== 4), [unsure])
    await card(page).getByRole('button', { name: 'Match with Swedebeat' }).click()
    await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
    const id = jobId(page)
    await untilState(page, id, 'completed')
    expect((await getJob(page, id)).error_category).toBeNull()

    await page.goto(`/classes/${classId}`)
    await expect(match(page)).toBeVisible(LONG)
    await expect(match(page).getByTestId('match-totals')).toHaveText('6 found, 1 unsure, 1 not in Swedebeat.')
    // The length counts the six found songs only: an unsure one is never counted as found.
    await expect(match(page).getByTestId('match-length')).toContainText('24:00 long')
    const songs = match(page).getByTestId('match-song')
    await expect(songs).toHaveCount(8)
    await expect(songs.nth(2)).toContainText('Not in Swedebeat')
    await expect(songs.nth(4)).toContainText('Unsure match')
    await expect(songs.nth(4)).toContainText('Swedebeat has: A Different Tune Entirely')
    await expect(match(page).getByText('the mix will lack it')).toBeVisible()
    await expect(nextStep(page)).toHaveCount(0) // nothing is approved yet

    // One search per distinct song, each with this user's own token.
    expect(catalogueCalls()).toEqual(Array<string>(8).fill(EMAIL_A))
    await check(page, 'match view: found, unsure and missing', match(page).getByRole('button', { name: 'Approve this selection' }))
  })

  await test.step('replace the two songs, approve the new list, and match again', async () => {
    useModelScript('e2e_swedebeat_replace')
    await match(page).getByRole('button', { name: 'Replace the missing and unsure songs' }).click()
    await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
    const id = jobId(page)
    await untilState(page, id, 'waiting_for_approval')
    const replaced = await listedTitles(page)
    expect(replaced).toHaveLength(8)
    expect(replaced[2]).not.toBe(ownTitles[2])
    expect(replaced[4]).not.toBe(ownTitles[4])
    expect(replaced.filter((_, i) => i !== 2 && i !== 4)).toEqual(ownTitles.filter((_, i) => i !== 2 && i !== 4))
    // The new list is a proposal the user approves once; nothing was matched yet.
    const before = catalogueCalls().length
    setCatalogue(replaced)
    useModelScript('revise_good')
    await page.getByRole('button', { name: 'Approve songs' }).click()
    await untilState(page, id, 'completed')
    expect(catalogueCalls().length).toBeGreaterThan(before)
    ownTitles = replaced
  })

  await test.step('approve the selection: nothing is written to Swedebeat', async () => {
    await page.goto(`/classes/${classId}`)
    await expect(match(page).getByTestId('match-totals')).toHaveText('8 found, 0 unsure, 0 not in Swedebeat.', LONG)
    await expect(match(page).getByRole('button', { name: 'Replace the missing and unsure songs' })).toHaveCount(0)
    await check(page, 'match view: all found', match(page).getByRole('button', { name: 'Approve this selection' }))

    await match(page).getByRole('button', { name: 'Approve this selection' }).click()
    await expect(match(page).getByText('Selection approved')).toBeVisible()
    await expect(match(page).getByText('Nothing was written to Swedebeat.')).toBeVisible()
    await expect(nextStep(page)).toBeVisible()
    // 8 songs of 4 minutes, less about 10 seconds for each of the 7 changes.
    await expect(nextStep(page).getByTestId('next-facts')).toContainText('8 songs, about 30:50 long as a mix')
    await expect(nextStep(page).getByTestId('next-facts')).toContainText('E2E matched ride')
    await expect(nextStep(page).getByRole('button', { name: 'I made the mix' })).toBeVisible()
    await expect(nextStep(page).getByRole('button', { name: 'Not now' })).toBeVisible()
    await check(page, 'next step: before the first check', nextStep(page).getByRole('button', { name: 'I made the mix' }))
  })

  await test.step('verify: no new mix yet, then a wrong-length mix the user picks', async () => {
    const connections = (await (await page.request.get('/api/connections')).json()) as {
      connections: { provider: string; id: string }[]
    }
    const mowl = connections.connections.find((c) => c.provider === 'mowl')!
    // The picked mix is the cached one (a map of 2 minutes), so the check needs no audio fetch.
    seedCachedMix(ownerIdForEmail(email), mowl.id, CACHED_MIX_ID)
    const before = swedebeatMixState()

    await nextStep(page).getByRole('button', { name: 'I made the mix' }).click()
    await expect(nextStep(page).getByTestId('verdict')).toContainText('Swedebeat has not shown a new mix yet', LONG)
    await expect(nextStep(page).getByRole('button', { name: 'Check again' })).toBeVisible()
    const picker = nextStep(page).getByRole('group', { name: 'Which one did you make?' })
    await expect(picker).toBeVisible()
    await check(page, 'next step: no new mix found', nextStep(page).getByRole('button', { name: 'Check again' }))

    await picker.getByRole('radio', { name: /E2E Cached Mix/ }).check({ force: true })
    await nextStep(page).getByRole('button', { name: 'Check again' }).click()
    await expect(nextStep(page).getByTestId('verdict')).toContainText('That mix is shorter than expected', LONG)
    await expect(nextStep(page).getByTestId('verdict')).toContainText('may be missing')
    await expect(nextStep(page).getByRole('button', { name: 'This is the right mix' })).toBeEnabled()
    await check(page, 'next step: a mix that is too short', nextStep(page).getByRole('button', { name: 'This is the right mix' }))

    // A cached analysis: no attach, no link and no audio fetch for the user's pick.
    const after = swedebeatMixState()
    expect(after.attaches).toBe(before.attaches)
    expect(after.links).toBe(before.links)
    expect(workerLogTail()).not.toContain('swedebeat media fetched')
  })

  await test.step('override: a verdict never blocks, and the class is designed on the mix', async () => {
    await nextStep(page).getByRole('button', { name: 'This is the right mix' }).click()
    await expect(nextStep(page).getByText('Mix verified')).toBeVisible()
    await check(page, 'next step: verified', nextStep(page).getByRole('button', { name: 'Design the class on this mix' }))

    const row = sql(`SELECT state || ',' || mix_id FROM swedebeat_handoffs WHERE class_id = '${classId}' AND superseded_at IS NULL`)
    expect(row).toBe(`verified,${CACHED_MIX_ID}`)

    useModelScript('e2e_swedebeat_mix')
    await nextStep(page).getByRole('button', { name: 'Design the class on this mix' }).click()
    await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
    const id = jobId(page)
    await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
    expect((await getJob(page, id)).state).toBe('completed')
    expect(sql(`SELECT music_source->>'source' || ',' || (music_source->>'mix_id') FROM classes WHERE id = '${classId}'`)).toBe(
      `mowl_swedebeat_mix,${CACHED_MIX_ID}`,
    )
  })

  await test.step('what leaked: nothing', async () => {
    const logs = workerLogTail(400)
    expect(logs).not.toContain(PASSWORD_A)
    expect(await page.content()).not.toContain(PASSWORD_A)
    // Only the catalogue searches carried the token; no login after the connect.
    expect(fakeState().logins.filter((l) => l.ok)).toHaveLength(1)
    expect(csp).toEqual([])
  })

  expect(findings.flatMap((f) => f.problems.map((p) => `${f.page}: ${p}`))).toEqual([])
  expect(findings.map((f) => f.page)).toHaveLength(6)
})

test('another user sees nothing of the match, the hand-off or the class', async ({ browser, baseURL }) => {
  const context = await stackContext(browser, baseURL!)
  const other = await context.newPage()
  try {
    const email = uniqueEmail('sb-match-other')
    await signUpThroughUi(other, email)
    await signInThroughUi(other, email)
    const revisions = sql(`SELECT id FROM class_revisions WHERE class_id = '${classId}' AND kind = 'swedebeat_match' ORDER BY number DESC LIMIT 1`)
    expect(revisions).toMatch(/^[0-9a-f-]{36}$/)
    for (const path of [
      `/api/classes/${classId}`,
      `/api/classes/${classId}/revisions/${revisions}`,
      `/api/classes/${classId}/swedebeat-handoff`,
    ]) {
      const answer = await other.request.get(path)
      expect(answer.status(), path).toBe(404)
      expect(await answer.text(), path).not.toContain(ownTitles[0]!)
    }
    await other.goto(`/classes/${classId}`)
    await expect(other.getByRole('region', { name: 'Swedebeat match' })).toHaveCount(0)
    await expect(other.getByRole('region', { name: 'Next, in the Swedebeat app' })).toHaveCount(0)
  } finally {
    await context.close()
  }
})
