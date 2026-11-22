import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, REV1, classRoutes } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { DRAFT_MIX, OTHER_DIGEST, PREVIEW_PATH, SUGGESTIONS, previewBody, suggestion } from '../test/mixFixtures'
import { renderAt } from '../test/render'

// ADR 0036 amendment B (#1373): the DJ mix opt-in on the consent screen. Fakes only; the preview
// is the server's read-only computation, so each test answers it the way the server would.

const PAGE = `/classes/${CLASS_ID}`
const HASH = 'cd'.repeat(32)
const LIST = 'e3333333-3333-4333-8333-333333333333'
const JOB = 'e4444444-4444-4444-8444-444444444444'
const CONNECTION = 'd1111111-1111-4111-8111-111111111111'

const WHOLE = { dj_mix: false, blend: false, trim: false, keep_whole: [], hard_cut_in: [], hard_cut_out: [] }
const DJ = { dj_mix: true, blend: true, trim: true, keep_whole: [], hard_cut_in: [], hard_cut_out: [] }

const TIMELINE = {
  revision_id: REV1,
  content_hash: HASH,
  ...DRAFT_MIX,
  duration_sec: 1320,
  workout_sec: 1200,
  tracks: [],
  crossfades: [
    { after: 0, sec: 8, mix_from_sec: 224, mix_to_sec: 232 },
    { after: 1, sec: 0, mix_from_sec: 400, mix_to_sec: 400 },
  ],
  intervals: [],
}

function routes(extra: Record<string, unknown> = {}, timeline: Record<string, unknown> = {}, cls: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { studio: true, ...cls } }),
    'GET /api/connections': {
      connections: [
        { id: CONNECTION, provider: 'swedebeat', status: 'connected', last_error: null, connected_at: '2026-10-01T10:00:00Z', last_used_at: null },
      ],
    },
    [`GET ${CLASS_PATH}/timeline`]: { ...TIMELINE, ...timeline },
    [`GET ${CLASS_PATH}/outputs`]: {
      enabled_outputs: ['spotify_playlist', 'swedebeat_playlist', 'swedebeat_mix', 'mowl'],
      outputs: [],
    },
    [`POST ${PREVIEW_PATH}`]: previewBody(),
    ...extra,
  }
}

type Api = ReturnType<typeof fakeApi>
const asked = (api: Api) => api.to('POST', PREVIEW_PATH).map((c) => (c.body as { choices: unknown }).choices)
const lastAsked = (api: Api) => asked(api).at(-1)
const mixOption = () => screen.getByRole('checkbox', { name: /^DJ mix/ })
const rows = () => screen.getAllByTestId('mix-song')

async function openMix(api: Api) {
  await screen.findByTestId('studio-outputs')
  fireEvent.click(mixOption())
  await screen.findAllByTestId('mix-song')
  await waitFor(() => { expect(lastAsked(api)).toEqual(DJ) })
}

describe('the DJ mix opt-in on the consent screen', () => {
  it('starts no job on open: whole songs is the default and the preview asks for whole songs', async () => {
    const api = fakeApi(routes())
    renderAt(PAGE)
    await screen.findByTestId('studio-outputs')
    await waitFor(() => { expect(asked(api)).toEqual([WHOLE]) })
    expect(mixOption()).not.toBeChecked()
    expect(screen.queryByTestId('mix-choices')).toBeNull()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
    expect(api.to('POST', `${CLASS_PATH}/outputs`)).toHaveLength(0)
    expect(await screen.findByRole('button', { name: 'Approve and create' })).toBeEnabled()
  })

  it('keeps Approve disabled while the preview for the current choices is awaited', async () => {
    fakeApi(routes({ [`POST ${PREVIEW_PATH}`]: () => new Promise<never>(() => {}) }))
    renderAt(PAGE)
    expect(await screen.findByRole('button', { name: 'Approve and create' })).toBeDisabled()
  })

  it('shows both switches on with DJ mix and asks for the DJ form', async () => {
    const api = fakeApi(routes())
    renderAt(PAGE)
    await openMix(api)
    expect(screen.getByRole('switch', { name: /Blend transitions/ })).toBeChecked()
    expect(screen.getByRole('switch', { name: /Trim songs/ })).toBeChecked()
    expect(rows()).toHaveLength(3)
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('changes the request with each switch and toggle, never applying anything', async () => {
    const api = fakeApi(routes())
    renderAt(PAGE)
    await openMix(api)
    fireEvent.click(within(rows()[0]!).getByRole('checkbox', { name: /Keep whole/ }))
    fireEvent.click(within(rows()[2]!).getByRole('checkbox', { name: /Hard cut in/ }))
    fireEvent.click(within(rows()[2]!).getByRole('checkbox', { name: /Hard cut out/ }))
    await waitFor(() => {
      expect(lastAsked(api)).toEqual({ ...DJ, keep_whole: [0], hard_cut_in: [2], hard_cut_out: [2] })
    })
    fireEvent.click(screen.getByRole('switch', { name: /Blend transitions/ }))
    await waitFor(() => { expect(lastAsked(api)).toMatchObject({ blend: false, trim: true }) })
    expect(within(rows()[2]!).queryByRole('checkbox', { name: /Hard cut in/ })).toBeNull()
    fireEvent.click(screen.getByRole('switch', { name: /Trim songs/ }))
    await waitFor(() => { expect(lastAsked(api)).toMatchObject({ blend: false, trim: false }) })
    expect(within(rows()[0]!).queryByRole('checkbox', { name: /Keep whole/ })).toBeNull()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('applies All, None and the actions on a selection', async () => {
    const api = fakeApi(routes())
    renderAt(PAGE)
    await openMix(api)
    expect(screen.getByRole('button', { name: 'Keep selected whole' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'All songs' }))
    expect(screen.getByText('3 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Keep selected whole' }))
    // Song 2 is full-only: it is whole anyway and is not put in the set.
    await waitFor(() => { expect(lastAsked(api)).toMatchObject({ keep_whole: [0, 2] }) })
    fireEvent.click(screen.getByRole('button', { name: 'Hard cut selected' }))
    await waitFor(() => { expect(lastAsked(api)).toMatchObject({ hard_cut_in: [0, 1, 2], hard_cut_out: [0, 1, 2] }) })
    fireEvent.click(screen.getByRole('button', { name: 'None' }))
    expect(screen.getByText('0 selected')).toBeInTheDocument()
    fireEvent.click(within(rows()[0]!).getByRole('checkbox', { name: /Select/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Back to the suggestion' }))
    await waitFor(() => { expect(lastAsked(api)).toMatchObject({ keep_whole: [2], hard_cut_in: [1, 2], hard_cut_out: [1, 2] }) })
    fireEvent.click(screen.getByRole('button', { name: 'All songs' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back to the suggestion' }))
    await waitFor(() => { expect(lastAsked(api)).toEqual(DJ) })
  })

  it('shows the proposal next to the full version and names the songs', async () => {
    const list = {
      revision_id: LIST, content_hash: HASH, target_sec: 1320, total_sec: 1320,
      evidence: { state: 'ok', sent: 0, missing: 0, anyway: false }, approvable: true,
      songs: [0, 1, 2].map((n) => ({
        position: n, title: `Wanted ${String(n)}`, artist: 'A', role: '', cover: null, gap: false,
        swedebeat: { title: `Song ${String(n)} title`, artist: 'Band', duration_sec: 245, bpm: 120 }, swap: null, can_undo: false, can_ask_another: false,
      })),
    }
    const api = fakeApi(
      routes({ [`GET ${CLASS_PATH}/studio-songs/${LIST}`]: list }, {}, { songs: { state: 'approved', job_id: null, revision_id: LIST, content_hash: HASH, expires_at: null } }),
    )
    renderAt(PAGE)
    await openMix(api)
    expect((await within(rows()[1]!).findAllByText(/Song 1 title/)).length).toBeGreaterThan(0)
    expect(within(rows()[0]!).getByTestId('mix-proposal')).toHaveTextContent('play 0:00 to 3:10 of 4:05, ends on a phrase')
  })

  it('says Full song only with the reason on a song that is not exact, with no trim toggle', async () => {
    const api = fakeApi(routes())
    renderAt(PAGE)
    await openMix(api)
    const row = rows()[1]!
    expect(within(row).getByTestId('mix-full-only')).toHaveTextContent('Full song only: its timing is only approximate.')
    expect(within(row).queryByRole('checkbox', { name: /Keep whole/ })).toBeNull()
    expect(within(row).getByRole('checkbox', { name: /Hard cut in/ })).toBeInTheDocument()
    expect(within(rows()[0]!).getByRole('checkbox', { name: /Keep whole/ })).toBeInTheDocument()
  })

  it('says that keeping a repeated song whole plays it once', async () => {
    const repeated = suggestion(0, { pieces: [{ start_sec: 0, end_sec: 120 }, { start_sec: 60, end_sec: 245 }], ends_on_phrase: [false, true] })
    const api = fakeApi(routes({ [`POST ${PREVIEW_PATH}`]: previewBody({ suggestions: [repeated, SUGGESTIONS[1], SUGGESTIONS[2]] }) }))
    renderAt(PAGE)
    await openMix(api)
    expect(within(rows()[0]!).getByTestId('mix-plays-once')).toHaveTextContent('Keeping it whole plays it once.')
    expect(within(rows()[2]!).queryByTestId('mix-plays-once')).toBeNull()
  })
})

describe('the new total, the refusal and the way out', () => {
  const OUT = previewBody({
    spec_digest: null, windows: null, crossfades: null, total_sec: 1000, refusal: { reason: 'class_length_out_of_tolerance' },
  })

  it('shows the total and the reason, disables Apply and offers the re-plan while a song can still be trimmed', async () => {
    const api = fakeApi(routes({ [`POST ${PREVIEW_PATH}`]: OUT }))
    renderAt(PAGE)
    await openMix(api)
    const total = await screen.findByText(/With these choices the mix is 16:40/)
    expect(total).toHaveTextContent('The class is 22:00; the mix must be between 19:48 and 24:12.')
    expect(await screen.findByRole('alert')).toHaveTextContent('too long or too short')
    expect(screen.getByRole('button', { name: 'Apply these choices' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Approve and create' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Re-plan the other songs' })).toBeEnabled()
    expect(screen.queryByRole('link', { name: 'Change songs' })).toBeNull()
  })

  it('offers Change songs instead of the re-plan when Trim songs is off', async () => {
    const api = fakeApi(routes({ [`POST ${PREVIEW_PATH}`]: OUT }))
    renderAt(PAGE)
    await openMix(api)
    fireEvent.click(screen.getByRole('switch', { name: /Trim songs/ }))
    expect(await screen.findByRole('link', { name: 'Change songs' })).toBeInTheDocument()
    await waitFor(() => { expect(screen.queryByRole('button', { name: 'Re-plan the other songs' })).toBeNull() })
  })

  it('offers Change songs when every trimmable song is kept whole', async () => {
    const api = fakeApi(routes({ [`POST ${PREVIEW_PATH}`]: OUT }))
    renderAt(PAGE)
    await openMix(api)
    fireEvent.click(screen.getByRole('button', { name: 'All songs' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep selected whole' }))
    expect(await screen.findByRole('link', { name: 'Change songs' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Re-plan the other songs' })).toBeNull()
  })

  it.each(['workout_does_not_fit', 'plan_not_allowed'])('always offers the re-plan for %s, even with Trim off', async (why) => {
    const api = fakeApi(routes({ [`POST ${PREVIEW_PATH}`]: previewBody({ spec_digest: null, windows: null, total_sec: null, refusal: { reason: why } }) }))
    renderAt(PAGE)
    await openMix(api)
    fireEvent.click(screen.getByRole('switch', { name: /Trim songs/ }))
    expect(await screen.findByRole('button', { name: 'Re-plan the other songs' })).toBeEnabled()
    expect(screen.getByRole('alert')).not.toHaveTextContent(why)
  })

  it('starts the re-plan only from its button, as a revise that carries the choices', async () => {
    const api = fakeApi(
      routes({
        [`POST ${PREVIEW_PATH}`]: OUT,
        'POST /api/jobs': () => json({ id: JOB, kind: 'studio_revise', state: 'queued' }, 201),
      }),
    )
    renderAt(PAGE)
    await openMix(api)
    fireEvent.click(within(rows()[0]!).getByRole('checkbox', { name: /Keep whole/ }))
    await waitFor(() => { expect(lastAsked(api)).toMatchObject({ keep_whole: [0] }) })
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
    fireEvent.click(await screen.findByRole('button', { name: 'Re-plan the other songs' }))
    await waitFor(() => { expect(api.to('POST', '/api/jobs')).toHaveLength(1) })
    const call = api.to('POST', '/api/jobs')[0]!
    expect(call.body).toMatchObject({
      kind: 'studio_revise',
      input: {
        class_id: CLASS_ID, base_revision_id: REV1, swedebeat_connection_id: CONNECTION,
        choices: { ...DJ, keep_whole: [0] },
      },
    })
    expect(call.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
  })

  it('offers the re-plan when there is no proposal to apply choices to, and asks for no preview', async () => {
    const api = fakeApi(routes({}, { proposal_revision_id: null }))
    renderAt(PAGE)
    expect(await screen.findByRole('button', { name: 'Re-plan the other songs' })).toBeEnabled()
    expect(screen.getByRole('alert')).toHaveTextContent('There is no design to choose from yet')
    expect(screen.getByRole('button', { name: 'Apply these choices' })).toBeDisabled()
    expect(api.to('POST', PREVIEW_PATH)).toHaveLength(0)
  })
})

describe('Apply these choices, then Approve', () => {
  const DIFFERENT = previewBody({ spec_digest: OTHER_DIGEST })

  it('keeps Approve off until the draft is the form on the screen, and starts no job until Apply is pressed', async () => {
    const api = fakeApi(routes({ [`POST ${PREVIEW_PATH}`]: DIFFERENT, 'POST /api/jobs': () => json({ id: JOB, kind: 'apply_mix_choices', state: 'queued' }, 201) }))
    renderAt(PAGE)
    const apply = await screen.findByRole('button', { name: 'Apply these choices' })
    await waitFor(() => { expect(apply).toBeEnabled() })
    expect(screen.queryByRole('button', { name: 'Approve and create' })).toBeNull()
    expect(screen.queryByTestId('studio-consent')).toBeNull()
    expect(screen.getByTestId('studio-apply-first')).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
    fireEvent.click(apply)
    await waitFor(() => { expect(api.to('POST', '/api/jobs')).toHaveLength(1) })
    expect(api.to('POST', '/api/jobs')[0]!.body).toEqual({
      kind: 'apply_mix_choices',
      input: { class_id: CLASS_ID, base_revision_id: REV1, base_content_hash: HASH, choices: WHOLE },
    })
    expect(api.to('POST', `${CLASS_PATH}/outputs`)).toHaveLength(0)
  })

  it('does not gate an approval that makes no SwedeBeat output', async () => {
    fakeApi(routes({ [`POST ${PREVIEW_PATH}`]: DIFFERENT }))
    renderAt(PAGE)
    await screen.findByTestId('studio-outputs')
    fireEvent.click(screen.getByRole('checkbox', { name: /^SwedeBeat playlist/ }))
    expect(await screen.findByRole('button', { name: 'Approve and create' })).toBeEnabled()
  })

  it('words a refusal of the apply job and offers the way out it names', async () => {
    const api = fakeApi(routes({ [`POST ${PREVIEW_PATH}`]: DIFFERENT, 'POST /api/jobs': () => reason(409, 'plan_class_too_long') }))
    renderAt(PAGE)
    await openMix(api)
    const apply = screen.getByRole('button', { name: 'Apply these choices' })
    await waitFor(() => { expect(apply).toBeEnabled() })
    fireEvent.click(apply)
    const alerts = await screen.findAllByRole('alert')
    expect(alerts[0]).toHaveTextContent('longer than your plan allows')
    expect(alerts[0]).not.toHaveTextContent('plan_class_too_long')
    expect(await screen.findByRole('button', { name: 'Re-plan the other songs' })).toBeInTheDocument()
  })

  it('approves the current draft with the choices summarised from its spec and the playlist following the mix', async () => {
    const trimmed = previewBody({
      windows: [
        { position: 0, start_sec: 0, end_sec: 190 },
        { position: 1, start_sec: 0, end_sec: 245 },
        { position: 2, start_sec: 10, end_sec: 245 },
      ],
    })
    const api = fakeApi(
      routes({
        [`POST ${PREVIEW_PATH}`]: trimmed,
        [`POST ${CLASS_PATH}/outputs`]: () => json({ id: JOB, kind: 'produce_outputs', state: 'queued' }, 201),
      }),
    )
    renderAt(PAGE)
    await openMix(api)
    const consent = await screen.findByTestId('studio-consent')
    expect(consent).toHaveTextContent('The SwedeBeat playlist follows the DJ mix’s cuts and crossfades.')
    expect(await screen.findByTestId('studio-mix-summary')).toHaveTextContent('DJ mix: songs trimmed 2, hard cuts 1.')
    fireEvent.click(await screen.findByRole('button', { name: 'Approve and create' }))
    await waitFor(() => { expect(api.to('POST', `${CLASS_PATH}/outputs`)).toHaveLength(1) })
    expect(api.to('POST', `${CLASS_PATH}/outputs`)[0]!.body).toEqual({
      revision_id: REV1,
      content_hash: HASH,
      outputs: ['spotify_playlist', 'swedebeat_playlist', 'swedebeat_mix'],
    })
  })

  it('pre-fills the recorded choices of an applied draft as toggles, without applying them', async () => {
    const recorded = { ...DJ, trim: false, hard_cut_in: [2] }
    const api = fakeApi(routes({}, { choices: recorded }))
    renderAt(PAGE)
    await screen.findAllByTestId('mix-song')
    expect(mixOption()).toBeChecked()
    expect(screen.getByRole('switch', { name: /Trim songs/ })).not.toBeChecked()
    await waitFor(() => { expect(lastAsked(api)).toEqual(recorded) })
    expect(within(rows()[2]!).getByRole('checkbox', { name: /Hard cut in/ })).toBeChecked()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })
})
