import { expect, test } from './helpers'
import { fakePlaylists } from './song-helpers'
import { LONG, designOnSpotdown, newStudioUser, outputStates } from './studio-flow-helpers'
import {
  DJ_DEFAULT,
  applyChoices,
  approveAndCreate,
  approvedReferences,
  cutLastSong,
  isPlain,
  mergePublishes,
  mixPlan,
  playlistBodies,
  previewOf,
  useStudioFakes,
} from './studio-mix-helpers'

// The DJ mix is the user's opt-in, with per-song choices (ADR 0036 amendment B, B12, #1374) on the
// fakes: nothing is analysed locally, the design runs on the fake spotdown's maps (studio-spotdown
// .spec.ts) and the fake gateway records what is written. The recorded design plays eight whole
// 240 s songs with 8 s crossfades; the screen's refusals are studio-dj-mix-refusals.spec.ts.

test.describe.configure({ mode: 'serial' })
test.setTimeout(480_000)
useStudioFakes()

const RATE = 44100 // the render plan's sample rate (output_body_policy.RATE)

test('whole songs by default: every track plays whole with no crossfade and no mix is made', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio-mix-whole')
  const classId = await designOnSpotdown(page, owner, 'Studio whole songs')
  await page.goto(`/classes/${classId}`)
  const outputs = page.getByTestId('studio-outputs')
  await expect(outputs.getByRole('checkbox', { name: /^DJ mix/ })).not.toBeChecked(LONG)
  expect(await isPlain(page, classId)).toBe(false) // the design's own draft is mixed: 8 s crossfades
  await applyChoices(page, classId) // the SwedeBeat playlist binds the whole-songs form of the draft
  expect(await isPlain(page, classId)).toBe(true)
  await approveAndCreate(page, classId, owner)

  const [body, ...more] = playlistBodies()
  expect(more).toEqual([])
  expect(body!.crossfade_sec).toBe(0)
  const references = approvedReferences(owner, classId)
  expect(body!.tracks.map((t) => t.reference).sort()).toEqual([...references].sort())
  for (const track of body!.tracks) {
    expect(track.start_sec).toBe(0)
    expect(track.end_sec).toBeCloseTo(240, 0) // the whole song, as long as it was analysed
    expect(track.trim).toBeNull() // no cut: the gateway's own default
    expect(track.crossfade_sec).toBe(0)
  }
  expect(mergePublishes()).toHaveLength(0)
  expect(outputStates(owner)).toBe('spotify_playlist:done,swedebeat_playlist:done')
  expect(fakePlaylists().filter((p) => p.name === 'Studio whole songs')).toHaveLength(1)
})

test('DJ mix with one song kept whole and one hard cut: what is written is what the preview showed', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio-mix-choices')
  const classId = await designOnSpotdown(page, owner, 'Studio mix choices')
  await cutLastSong(page, classId) // the proposal now trims a song, so keeping it whole shows
  const references = approvedReferences(owner, classId)
  const proposal = await previewOf(page, classId, DJ_DEFAULT) // classbeat's suggestion: what the others keep
  const suggested = proposal.suggestions
  const trimmed = suggested.filter((s) => s.pieces.some((p) => p.end_sec < s.length_sec - 0.5)).map((s) => s.position)
  expect(trimmed).toHaveLength(1)
  const whole = trimmed[0]!
  const hard = whole === 4 ? 3 : 4

  const outputs = page.getByTestId('studio-outputs')
  await outputs.getByRole('checkbox', { name: /^DJ mix/ }).check()
  const rows = page.getByTestId('mix-song')
  await expect(rows).toHaveCount(8, LONG)
  await expect(rows.nth(whole).getByTestId('mix-proposal')).toContainText(/play 0:\d\d to 3:\d\d of 4:00/)
  await rows.nth(whole).getByRole('checkbox', { name: /^Keep whole/ }).check()
  await rows.nth(hard).getByRole('checkbox', { name: /^Hard cut in/ }).check()
  await expect(rows.nth(whole).getByTestId('mix-result')).toContainText('play the whole song (4:00)')
  await applyChoices(page, classId)
  await expect(outputs.getByRole('checkbox', { name: /^DJ mix/ })).toBeChecked(LONG) // the applied choices come back
  await expect(page.getByTestId('studio-consent')).toContainText('SwedeBeat playlist, DJ mix')
  await expect(page.getByTestId('studio-mix-summary')).toContainText('hard cuts 1')

  // The preview of the same choices is the form the screen showed and the draft now is.
  const choices = { ...DJ_DEFAULT, keep_whole: [whole], hard_cut_in: [hard] }
  const shown = await previewOf(page, classId, choices)
  expect(shown.refusal).toBeNull()
  const windows = shown.windows!
  const fades = shown.crossfades!
  const at = windows.findIndex((w) => w.position === hard)
  expect(at).toBeGreaterThan(0)
  // The hard cut is the change into that song; every other crossfade is the suggestion's.
  expect(fades).toEqual(proposal.crossfades!.map((f, n) => (n === at - 1 ? 0 : f)))
  expect(fades.filter((f) => f === 0)).toHaveLength(1)
  for (const w of windows) {
    const song = suggested[w.position]!
    const [from, to] = w.position === whole ? [0, song.length_sec] : [song.pieces[0]!.start_sec, song.pieces[0]!.end_sec]
    expect(w.start_sec).toBeCloseTo(from, 1)
    expect(w.end_sec).toBeCloseTo(to, 1)
  }

  await approveAndCreate(page, classId, owner)
  expect(outputStates(owner)).toBe('spotify_playlist:done,swedebeat_mix:done,swedebeat_playlist:done')
  // The SwedeBeat playlist follows the mix: the preview's windows and crossfades, in mix order.
  const [body, ...more] = playlistBodies()
  expect(more).toEqual([])
  expect(body!.tracks.map((t) => t.reference)).toEqual(windows.map((w) => references[w.position]))
  body!.tracks.forEach((t, n) => {
    expect(t.start_sec).toBeCloseTo(windows[n]!.start_sec, 1)
    expect(t.end_sec).toBeCloseTo(windows[n]!.end_sec, 1)
    expect(t.crossfade_sec).toBe(n < fades.length ? fades[n] : 0)
    expect(t.trim).toBeNull() // every song plays whole
  })
  // The mix was rendered from the same plan, and merge-published once.
  const plan = mixPlan(owner)
  expect(plan.map((p) => p.reference)).toEqual(body!.tracks.map((t) => t.reference))
  plan.forEach((p, n) => {
    expect((p.src_end - p.src_start) / RATE).toBeCloseTo(windows[n]!.end_sec - windows[n]!.start_sec, 1)
    expect(p.fade_out / RATE).toBeCloseTo(n < fades.length ? fades[n]! : 0, 1)
  })
  expect(mergePublishes()).toHaveLength(1)
})

test('with the bass swap off, the rendered mix has no curve on any transition', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio-mix-curve')
  const classId = await designOnSpotdown(page, owner, 'Studio mix no curve')
  await page.goto(`/classes/${classId}`)
  const outputs = page.getByTestId('studio-outputs')
  await outputs.getByRole('checkbox', { name: /^DJ mix/ }).check() // the default choices: the draft already is that form
  const fades = (await previewOf(page, classId, DJ_DEFAULT)).crossfades!
  expect(fades).toHaveLength(7)
  await approveAndCreate(page, classId, owner)

  expect(outputStates(owner)).toBe('spotify_playlist:done,swedebeat_mix:done,swedebeat_playlist:done')
  const plan = mixPlan(owner)
  expect(plan).toHaveLength(8)
  for (const entry of plan) expect(Object.keys(entry)).not.toContain('curve') // `bass_swap` is off in the stack: equal power
  plan.slice(0, 7).forEach((p, n) => { expect(p.fade_out / RATE).toBeCloseTo(fades[n]!, 1) }) // the design's crossfades
  expect(mergePublishes()).toHaveLength(1)
})
