import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { decodeJob } from '../../api/jobs'
import { fakeApi, json, reason } from '../../test/fakeApi'
import { CLASS_ID, JOB_ID, jobBody } from '../../test/jobFixtures'
import { RouterProvider } from '../../router/Router'
import { StudioSongs } from './StudioSongs'
import { StudioEnded } from './StudioEnded'
import { rememberStudioAction } from './studioAction'

const LIST = '55555555-5555-4555-8555-555555555555'
const NEW_JOB = '66666666-6666-4666-8666-666666666666'
const PATH = `/api/classes/${CLASS_ID}/studio-songs/${LIST}`

function song(position: number, extra: Record<string, unknown> = {}) {
  return {
    position,
    title: `Title ${String(position)} <b>x</b>`,
    artist: `Artist ${String(position)}`,
    role: 'climb',
    cover: null,
    gap: false,
    swedebeat: { title: 'SB', artist: 'SB artist', duration_sec: 241, bpm: 127.6 },
    swap: null,
    can_undo: false,
    can_ask_another: false,
    ...extra,
  }
}

const SWAPPED = song(1, {
  swap: {
    original: { title: 'Old song', artist: 'Old artist' },
    replacement: { title: 'New song', artist: 'New artist' },
    reason: 'missing',
    score: 0.9,
    undone: false,
  },
  can_undo: true,
  can_ask_another: true,
})
const GAP = song(2, {
  gap: true,
  swedebeat: null,
  swap: {
    original: { title: 'Lost', artist: 'Gone' },
    replacement: { title: 'New song', artist: 'New artist' },
    reason: 'asked',
    score: 0.9,
    undone: true,
  },
  can_ask_another: true,
})

function view(extra: Record<string, unknown> = {}) {
  return {
    revision_id: LIST,
    content_hash: 'ab'.repeat(32),
    target_sec: 600,
    total_sec: 580,
    evidence: { state: 'ok', sent: 3, missing_first: 0, missing: 0, anyway: false },
    rounds: 1,
    note: '',
    approvable: false,
    songs: [song(0), SWAPPED, GAP],
    ...extra,
  }
}

const job = (state = 'waiting_for_approval') =>
  decodeJob(jobBody(state, { kind: 'studio_songs', approval_revision_id: LIST }))

function mount(state?: string) {
  return render(
    <RouterProvider>
      <StudioSongs job={job(state)} />
    </RouterProvider>,
  )
}

beforeEach(() => {
  window.sessionStorage.clear()
})

describe('StudioSongs', () => {
  it('shows facts, gaps and each swap as original to replacement with its reason', async () => {
    fakeApi({ [`GET ${PATH}`]: view() })
    mount()
    expect(await screen.findAllByTestId('studio-song')).toHaveLength(3)
    expect(screen.getAllByText('4:01, 128 BPM')).toHaveLength(2)
    expect(screen.getByText('Title 0 <b>x</b>')).toBeInTheDocument()
    expect(screen.getByText('Old song, Old artist → New song, New artist')).toBeInTheDocument()
    expect(screen.getByText('Swapped because SwedeBeat does not have the original.')).toBeInTheDocument()
    expect(screen.getByText('You undid this swap. Lost is a gap now.')).toBeInTheDocument()
    expect(screen.getByText(/One song is a gap/)).toBeInTheDocument()
    expect(screen.getByTestId('studio-credit')).toHaveTextContent('A new pick costs a class credit.')
    expect(screen.queryByTestId('studio-changes')).toBeNull()
  })

  it('shows the change count when the view carries it', async () => {
    fakeApi({ [`GET ${PATH}`]: view({ changes: { count: 2, limit: 5 } }) })
    mount()
    expect(await screen.findByTestId('studio-changes')).toHaveTextContent('2 of 5')
  })

  it('sends Undo with a position and an Idempotency-Key, then follows the new job', async () => {
    const api = fakeApi({
      [`GET ${PATH}`]: view(),
      [`POST ${PATH}/undo`]: json({ id: NEW_JOB, kind: 'studio_songs', state: 'queued' }, 201),
    })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Undo the swap of Title 1 <b>x</b>' }))
    await waitFor(() => { expect(api.to('POST', `${PATH}/undo`)).toHaveLength(1) })
    const call = api.to('POST', `${PATH}/undo`)[0]!
    expect(call.body).toEqual({ position: 1 })
    expect(call.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
  })

  it('asks for another song at a gap', async () => {
    const api = fakeApi({
      [`GET ${PATH}`]: view(),
      [`POST ${PATH}/another`]: json({ id: NEW_JOB, kind: 'studio_songs', state: 'queued' }, 201),
    })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Ask for another song instead of Title 2 <b>x</b>' }))
    await waitFor(() => { expect(api.to('POST', `${PATH}/another`)[0]?.body).toEqual({ position: 2 }) })
  })

  it('words revision_limit with the counts and keeps the count on the page', async () => {
    fakeApi({
      [`GET ${PATH}`]: view(),
      [`POST ${PATH}/undo`]: () => json({ detail: { reason: 'revision_limit', limit: 5, count: 5 } }, 409),
    })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /Undo the swap/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('has used 5 of its 5 song changes')
    expect(screen.getByTestId('studio-changes')).toHaveTextContent('5 of 5')
    expect(document.body).not.toHaveTextContent('revision_limit')
  })

  it.each([
    ['list_has_gap', 409, 'still has a gap'],
    ['quota', 409, 'no room for this'],
    ['idempotency_mismatch', 409, 'already used for something else'],
  ])('words the refusal %s in plain text', async (code, status, words) => {
    fakeApi({ [`GET ${PATH}`]: view(), [`POST ${PATH}/undo`]: () => reason(status, code) })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /Undo the swap/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(words)
    expect(document.body).not.toHaveTextContent(code)
  })

  it('offers Try again later and Swap anyway for a suspected outage', async () => {
    const api = fakeApi({
      [`GET ${PATH}`]: view({ evidence: { state: 'suspected_unavailable', sent: 5, missing_first: 5, missing: 5, anyway: false } }),
      [`POST /api/jobs/${JOB_ID}/cancel`]: jobBody('cancelled'),
      [`POST ${PATH}/swap-anyway`]: json({ id: NEW_JOB, kind: 'studio_songs', state: 'queued' }, 201),
    })
    mount()
    expect(await screen.findByText('SwedeBeat found none of these songs')).toBeInTheDocument()
    expect(screen.getByText(/none of the 5 songs/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Swap anyway' }))
    await waitFor(() => { expect(api.to('POST', `${PATH}/swap-anyway`)).toHaveLength(1) })
    expect(api.to('POST', `${PATH}/swap-anyway`)[0]!.body).toEqual({})
    fireEvent.click(screen.getByRole('button', { name: 'Try again later' }))
    await waitFor(() => { expect(api.to('POST', `/api/jobs/${JOB_ID}/cancel`)).toHaveLength(1) })
    expect(await screen.findByText(/Nothing was changed/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Swap anyway' })).toBeNull()
  })

  it('does not offer the actions for a job that is not waiting or ended', async () => {
    fakeApi({ [`GET ${PATH}`]: view() })
    mount('running')
    await screen.findAllByTestId('studio-song')
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
  })

  it('says a studio list could not be loaded in plain words', async () => {
    fakeApi({ [`GET ${PATH}`]: () => reason(404, 'not_found') })
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be loaded')
  })
})

describe('StudioEnded', () => {
  const failed = (category: string) =>
    decodeJob(jobBody('failed', { kind: 'studio_songs', error_category: category }))
  const mountEnded = (category: string) =>
    render(
      <RouterProvider>
        <StudioEnded job={failed(category)} />
      </RouterProvider>,
    )

  it('explains a failed another and offers a new pick that costs a credit', () => {
    rememberStudioAction(JOB_ID, 'another')
    mountEnded('track_unavailable')
    expect(screen.getByText('A song file is not available')).toBeInTheDocument()
    expect(screen.getByTestId('studio-no-list')).toHaveTextContent('earlier list was cancelled')
    expect(screen.getByTestId('studio-new-pick')).toHaveTextContent('costs a class credit')
    expect(screen.getByRole('link', { name: 'Start a new pick' })).toHaveAttribute('href', expect.stringContaining('/classes/new'))
  })

  it('does not claim a cancelled list for a failed first pick', () => {
    mountEnded('track_limit')
    expect(screen.queryByTestId('studio-no-list')).toBeNull()
    expect(screen.getByText('Today’s limit of song files is reached')).toBeInTheDocument()
  })

  describe('approval', () => {
    const DESIGN_JOB = '77777777-7777-4777-8777-777777777777'
    const APPROVE = `/api/jobs/${JOB_ID}/approve`

    it('approves the list, starts the design with a key and follows its job', async () => {
      const api = fakeApi({
        [`GET ${PATH}`]: view({ approvable: true }),
        [`POST ${APPROVE}`]: jobBody('completed', { kind: 'studio_songs' }),
        [`POST ${PATH}/design`]: json({ id: DESIGN_JOB, kind: 'studio_design', state: 'queued' }, 201),
      })
      mount()
      fireEvent.click(await screen.findByRole('button', { name: 'Approve songs' }))
      await waitFor(() => { expect(api.to('POST', `${PATH}/design`)).toHaveLength(1) })
      expect(api.to('POST', APPROVE)[0]!.body).toEqual({ revision_id: LIST, content_hash: 'ab'.repeat(32) })
      const design = api.to('POST', `${PATH}/design`)[0]!
      expect(design.body).toEqual({})
      expect(design.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    })

    it('offers no approval for a list that cannot be approved', async () => {
      fakeApi({ [`GET ${PATH}`]: view({ approvable: false }) })
      mount()
      await screen.findAllByTestId('studio-song')
      expect(screen.queryByRole('button', { name: 'Approve songs' })).toBeNull()
    })

    it('retries only the design start when it failed after the approval', async () => {
      let designs = 0
      const api = fakeApi({
        [`GET ${PATH}`]: view({ approvable: true }),
        [`POST ${APPROVE}`]: jobBody('completed', { kind: 'studio_songs' }),
        [`POST ${PATH}/design`]: () => {
          designs += 1
          return designs === 1
            ? reason(409, 'already_designed')
            : json({ id: DESIGN_JOB, kind: 'studio_design', state: 'queued' }, 201)
        },
      })
      mount()
      fireEvent.click(await screen.findByRole('button', { name: 'Approve songs' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('already designed')
      expect(screen.getByRole('alert')).toHaveTextContent('did not start')
      fireEvent.click(screen.getByRole('button', { name: 'Start the design' }))
      await waitFor(() => { expect(api.to('POST', `${PATH}/design`)).toHaveLength(2) })
      expect(api.to('POST', APPROVE)).toHaveLength(1)
    })

    it('words a refused approval and keeps the list', async () => {
      fakeApi({
        [`GET ${PATH}`]: view({ approvable: true }),
        [`POST ${APPROVE}`]: () => reason(409, 'list_has_gap'),
      })
      mount()
      fireEvent.click(await screen.findByRole('button', { name: 'Approve songs' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('gap')
      expect(screen.getAllByTestId('studio-song')).toHaveLength(3)
    })
  })
})
