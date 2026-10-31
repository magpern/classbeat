import { expect, test } from './helpers'
import { PLAYLISTS, fakeMowl } from './design-helpers'
import {
  LONG,
  FREE,
  burst,
  cancelJob,
  contextsOf,
  createDesign,
  endedAs,
  expectClasses,
  expectHeld,
  newUser,
  statusesOf,
} from './m5-helpers'

// M5 T1 (#228, ADR 0011 section 10), the done-when "limits hold under concurrency and refunds
// work", on the compose stack with the fakes (fake MOWL, FakeProvider replaying
// good_first_draft). Free is three classes a month, one job at a time. Parallel calls come from
// several browser contexts that hold one user's session. The backend suites cover the same races
// one layer down (test_m5_done_when.py, test_quota_jobs.py); this is the whole path: the web app,
// the queue, the worker's settle and the account page.
test.setTimeout(300_000)

test('parallel creates start the plan limit, then the month is full, and two users never mix', async ({
  browser,
  baseURL,
}) => {
  const a = await newUser(browser, baseURL, 'm5-a')
  const b = await newUser(browser, baseURL, 'm5-b')
  const mine = await contextsOf(browser, a.page, 3)
  const his = await contextsOf(browser, b.page, 3)

  for (const round of [1, 2]) {
    // Both users fire six creates at once: free allows one job at a time, so one starts and the
    // other five are busy, each with the plan's limit.
    const [fromA, fromB] = await Promise.all([burst(mine, a.connectionId, 6), burst(his, b.connectionId, 6)])
    for (const answers of [fromA, fromB]) {
      expect(statusesOf(answers)).toEqual([201, 429, 429, 429, 429, 429])
      for (const busy of answers.filter((x) => x.status === 429)) {
        expect(busy.body).toEqual({ detail: { reason: 'busy', limit: FREE.concurrent } })
      }
    }
    const ids = [fromA, fromB].map((answers) => answers.find((x) => x.status === 201)!.body.id!)
    expect(await Promise.all([endedAs(a.page, ids[0]!), endedAs(b.page, ids[1]!)])).toEqual(['completed', 'completed'])
    await expectClasses(a.page, round, FREE.classes)
    await expectClasses(b.page, round, FREE.classes)
  }

  // A's third class, on its own: B stays at two.
  const third = await burst(mine, a.connectionId, 6)
  expect(statusesOf(third)).toEqual([201, 429, 429, 429, 429, 429])
  await endedAs(a.page, third.find((x) => x.status === 201)!.body.id!)
  await expectClasses(a.page, 3, FREE.classes)
  await expectClasses(b.page, 2, FREE.classes)

  // A's month is full: every create is refused, whichever context sends it, with the numbers and
  // nothing else; B (two classes) starts its third, one of six, in the same moment.
  const [full, free] = await Promise.all([burst(mine, a.connectionId, 6), burst(his, b.connectionId, 6)])
  expect(statusesOf(full)).toEqual([409, 409, 409, 409, 409, 409])
  for (const refused of full) {
    expect(refused.body.detail).toMatchObject({ reason: 'quota', quota: 'classes', limit: 3, used: 3, plan: 'free' })
    expect(refused.body.detail?.period_end).toMatch(/^\d{4}-\d{2}-01$/)
    expect(Object.keys(refused.body.detail!).sort()).toEqual(['limit', 'period_end', 'plan', 'quota', 'reason', 'used'])
  }
  expect(statusesOf(free)).toEqual([201, 429, 429, 429, 429, 429])
  await endedAs(b.page, free.find((x) => x.status === 201)!.body.id!)
  await expectClasses(a.page, 3, FREE.classes)
  await expectClasses(b.page, 3, FREE.classes)

  for (const user of [a, b]) await user.context.close()
})

test('a failed design and a cancelled one give the credit back, a finished one keeps it', async ({
  browser,
  baseURL,
}) => {
  const u = await newUser(browser, baseURL, 'm5-refund')
  const [ctx] = await contextsOf(browser, u.page, 1)

  // The fake MOWL loses the attach: the job fails and the class was not delivered.
  fakeMowl('arm', PLAYLISTS.flaky.id)
  const failing = await createDesign(ctx!, u.connectionId, PLAYLISTS.flaky)
  expect(failing.status).toBe(201)
  expect(await endedAs(u.page, failing.body.id!)).toBe('failed')
  await expectHeld(u.page, 0)

  // A cancel while the run is certainly in flight (the fake holds its first read).
  fakeMowl('arm', PLAYLISTS.slow.id)
  const slow = await createDesign(ctx!, u.connectionId, PLAYLISTS.slow)
  expect(slow.status).toBe(201)
  await expect.poll(() => fakeMowl('state', PLAYLISTS.slow.id).held, LONG).toBe(1)
  await expectHeld(u.page, 1) // the credit is taken while the run goes
  await cancelJob(u.page, slow.body.id!)
  fakeMowl('release', PLAYLISTS.slow.id)
  expect(await endedAs(u.page, slow.body.id!)).toBe('cancelled')
  await expectHeld(u.page, 0)

  // A class that is delivered keeps its credit, and the page says so.
  fakeMowl('arm', PLAYLISTS.draft.id)
  const good = await createDesign(ctx!, u.connectionId, PLAYLISTS.draft)
  expect(good.status).toBe(201)
  expect(await endedAs(u.page, good.body.id!)).toBe('completed')
  await expectHeld(u.page, 1)
  await expectClasses(u.page, 1, FREE.classes)
  await u.context.close()
})
