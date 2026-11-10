import { expect, test } from './helpers'
import { PLAYLISTS, fakeMowl } from './design-helpers'
import { useModelScript } from './model-script'
import { LONG, designAndOpen, reviseByChat, saveArm, signedInWithMowl } from './class-helpers'
import { THEMES, audit, setTheme } from './mu-helpers'

// The class history (ADR 0007 amendment of 2026-11-03, part H; issue #546): the timeline of a
// class's versions, the comparison of two and Make current, on the fakes (the fake MOWL and the
// worker's FakeProvider scripts), with the axe audit of the timeline and the comparison. The fakes'
// state is shared, so this file runs on its own after the others' serial order.
test.describe.configure({ mode: 'serial' })
test.setTimeout(180_000)

const HISTORY = 'section[aria-labelledby="revisions-title"]'

test.beforeEach(() => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true)
})

test.afterAll(() => {
  useModelScript('good_first_draft')
})

test('the timeline, a comparison and making an older version current', async ({ page }) => {
  await signedInWithMowl(page, 'history')
  await designAndOpen(page, 'E2E history', PLAYLISTS.draft.name)

  // PP1 (ADR 0030): zone colours come from classes, not `style`, in both themes. A bar is filled by
  // its zone's gradient, whose first stop is the zone colour the token gives.
  for (const theme of THEMES) {
    await setTheme(page, theme)
    const found = await page.locator('[data-testid="profile"] rect[data-bar]').first().evaluate((rect) => {
      const gradient = document.querySelector(rect.getAttribute('fill')?.slice(4, -1) ?? '')
      const probe = document.createElement('div')
      probe.style.color = `var(--cb-zone-${rect.getAttribute('data-zone') ?? ''})`
      document.body.append(probe)
      const expected = getComputedStyle(probe).color
      probe.remove()
      const stop = gradient?.querySelector('stop')
      return {
        expected,
        stop: stop ? getComputedStyle(stop).stopColor : null,
        styled: document.querySelectorAll('[data-testid="profile"] [style]').length,
      }
    })
    expect(found.stop, theme).toBe(found.expected)
    expect(found.styled, theme).toBe(0)
  }

  await reviseByChat(page, 'revise_good', 'Make the first minutes easier.', 2)

  const history = page.locator(HISTORY)
  const entries = history.getByTestId('history-entry')
  await expect(entries).toHaveCount(2, LONG)
  const v1 = entries.filter({ has: page.getByText('Version 1', { exact: true }) })
  const v2 = entries.filter({ has: page.getByText('Version 2', { exact: true }) })
  await expect(v2.getByText('You asked: “Make the first minutes easier.”')).toBeVisible()
  await expect(v2.getByText('Current')).toBeVisible()
  await expect(v1.getByText('First design')).toBeVisible()
  await expect(v1).toContainText('TSS')
  await expect(v1.getByText('Current')).toHaveCount(0)

  // Compare the two in plain words.
  await history.getByRole('button', { name: 'Compare version 1' }).click()
  await history.getByRole('button', { name: 'Compare version 2' }).click()
  await history.getByRole('button', { name: 'Compare versions 1 and 2' }).click()
  const comparison = history.getByTestId('comparison')
  await expect(comparison.getByRole('heading', { name: 'Version 1 to version 2' })).toBeVisible()
  await expect(comparison).toContainText('Length:')
  await expect(comparison).toContainText('TSS:')
  await expect(comparison).toContainText('Music: the same.')

  const findings = await audit(page, 'class history with a comparison', comparison.getByRole('heading'), HISTORY)
  expect(findings.problems).toEqual([])

  // Make version 1 current (no model, no credit), then version 2 again.
  await history.getByRole('button', { name: 'Use this version 1' }).click()
  await expect(v1.getByText('Current')).toBeVisible()
  await expect(v2.getByText('Current')).toHaveCount(0)
  await history.getByRole('button', { name: 'Use this version 2' }).click()
  await expect(v2.getByText('Current')).toBeVisible()
  await expect(history.getByRole('button', { name: 'Use this version 2' })).toHaveCount(0)
})
