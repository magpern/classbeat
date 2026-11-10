import { expect, test } from './helpers'
import { PLAYLISTS, disconnectMowl, fakeMowl } from './design-helpers'
import { saveArm } from './class-helpers'
import { audit } from './mu-helpers'
import { LONG, designFromWords, holdHandoff, instructorWithMowl, pickMusic, say } from './ai-first-helpers'
import { useModelScript } from './model-script'

// MA5 (ADR 0029, ADR 0013 section 10): the AI-first pages pass the accessibility audit at 390 and
// 1440 px in the light and the dark theme: no sideways scroll, the primary action in the window and
// no serious or critical axe violation.

test.beforeEach(() => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true)
})

test('the start screen, a designed class with a game and a request beyond the plan pass the audit', async ({
  page,
}) => {
  test.setTimeout(300_000)
  await instructorWithMowl(page, 'aia11y')
  await say(page, 'A 30 minute ride with two climbs and a Power Pull after climb 2.')
  const start = await audit(page, 'Start screen', page.getByRole('button', { name: 'Design my class' }))
  expect(start.problems).toEqual([])

  await page.setViewportSize({ width: 1280, height: 720 })
  await designFromWords(
    page,
    'A 30 minute ride with two climbs and a Power Pull after climb 2, 2 minutes long.',
    { reading: 'ai_first_read_pull', design: 'ai_first_design_pull' },
    { music: PLAYLISTS.draft.name },
  )
  await expect(page.getByRole('region', { name: 'Game' }).getByText('Power Pull')).toBeVisible(LONG)
  const designed = await audit(page, 'Class with a game', page.getByTestId('class-header').getByRole('button', { name: 'Save to MOWL' }))
  expect(designed.problems).toEqual([])

  await page.setViewportSize({ width: 1280, height: 720 })
  await designFromWords(
    page,
    'A 90 minute ride with two climbs.',
    { reading: 'ai_first_read_long', design: 'ai_first_design_long' },
    { music: 'No music' },
  )
  await expect(page.getByTestId('interpretation').getByText('Not as asked')).toBeVisible(LONG)
  await expect(page.getByRole('heading', { name: /Invented/ })).toBeVisible(LONG)
  const notAsked = await audit(page, 'Class with a request beyond the plan', page.getByTestId('class-header').getByRole('button', { name: 'Save to MOWL' }))
  expect(notAsked.problems).toEqual([])
})

test('a refused hand-off passes the audit', async ({ page }) => {
  test.setTimeout(240_000)
  await instructorWithMowl(page, 'aia11yrefuse')
  useModelScript('ai_first_read_pull')
  const held = await holdHandoff(page)
  await say(page, 'A 30 minute ride with two climbs and a Power Pull after climb 2.')
  await pickMusic(page, PLAYLISTS.draft.name)
  await page.getByRole('button', { name: 'Design my class' }).click()
  await expect(page.getByTestId('interpretation')).toBeVisible(LONG)
  const other = await page.context().newPage()
  await disconnectMowl(other)
  await other.close()
  await page.bringToFront() // the audit waits on animation frames, which a hidden tab does not run
  held.release()
  const stopped = page.getByTestId('handoff-stopped')
  await expect(stopped).toBeVisible(LONG)
  // On a phone the Chat and Class panels are tabs: audit the reading first, then the refusal.
  await page.setViewportSize({ width: 390, height: 844 })
  const reading = await audit(page, 'Reading of a refused request', page.getByTestId('interpretation'))
  expect(reading.problems).toEqual([])
  await page.setViewportSize({ width: 390, height: 844 }) // the audit ends at 1440 px, where there are no tabs
  await page.getByRole('tab', { name: 'Class', exact: true }).click()
  const refused = await audit(page, 'Refused hand-off', stopped.getByRole('link', { name: 'Go to your account' }))
  expect(refused.problems).toEqual([])
})
