import userEvent from '@testing-library/user-event'
import { act, fireEvent, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useJobProgress } from '../hooks/useJobProgress'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, DRAFT, JOB_ID, OPERATION_ID, SESSION_ROUTES, jobBody, openStream, sse } from '../test/jobFixtures'
import { renderAt } from '../test/render'

const JOB = `/api/jobs/${JOB_ID}`
const OWN = `GET /api/external-operations?job_id=${JOB_ID}`
const ACCOUNT = 'GET /api/external-operations'
const failed = jobBody('failed', { error_category: 'uncertain_write' })
const uncertain = { id: OPERATION_ID, kind: 'mowl_attach_spotify_playlist', state: 'uncertain', job_id: null, resolved_by: null }
const INPUT = {
  title: 'Hills',
  brief: { length_min: 45, intensity: 'moderate', climbs: 1, sprints: 0, races: 0, ftp_test: null, style: '', text: '' },
  music: { source: 'mowl_spotify_playlist', spotify_playlist_id: 'AbCdEfGhIjKlMnOpQrStUv' },
  mowl_connection_id: 'c0000000-0000-4000-8000-000000000001',
}
const remember = () => {
  window.sessionStorage.setItem(`classbeat.design-input.${JOB_ID}`, JSON.stringify(INPUT))
}

afterEach(() => {
  window.sessionStorage.clear()
})

describe('an uncertain attach whose list is empty or unreadable', () => {
  it('after a 409 and an empty refresh the Design again button is reachable', async () => {
    remember()
    let settled = false
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OWN]: () => ({ operations: settled ? [] : [uncertain] }),
      [ACCOUNT]: { operations: [] },
      [`POST /api/external-operations/${OPERATION_ID}/check-again`]: () => {
        settled = true
        return reason(409, 'not_uncertain')
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByText(/already settled/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Design again' })).toBeInTheDocument()
  })

  it('falls back to the account list when the job has none and acts on that row', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OWN]: { operations: [] },
      [ACCOUNT]: { operations: [uncertain] },
      [`POST /api/external-operations/${OPERATION_ID}/check-again`]: { ...uncertain, state: 'succeeded' },
    })
    renderAt(`/jobs/${JOB_ID}`)
    const scope = await screen.findByTestId('account-scope')
    expect(scope).toHaveTextContent('from an earlier design of yours.')
    expect(scope).not.toHaveTextContent('same playlist')
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByText(/playlist is in your MOWL music/)).toBeInTheDocument()
    expect(api.to('POST', `/api/external-operations/${OPERATION_ID}/check-again`)).toHaveLength(1)
  })

  it('with nothing listed anywhere it is honest: disconnect and reconnect, with the warning', async () => {
    remember()
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OWN]: { operations: [] },
      [ACCOUNT]: { operations: [] },
    })
    renderAt(`/jobs/${JOB_ID}`)
    const note = await screen.findByTestId('no-undecided')
    expect(note).toHaveAttribute('role', 'status')
    expect(note).toHaveTextContent('disconnect MOWL and connect it again')
    expect(note).toHaveTextContent('a second time')
    expect(note).not.toHaveTextContent('checks the playlist once more')
    expect(screen.getByRole('link', { name: 'your account page' })).toHaveAttribute('href', '/account')
    expect(await screen.findByRole('button', { name: 'Design again' })).toBeInTheDocument()
  })

  it('a failed list read is an error with a retry, never "nothing to decide"', async () => {
    let reads = 0
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OWN]: () => {
        reads += 1
        return reads === 1 ? json({}, 500) : { operations: [uncertain] }
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText('What is left to decide could not be loaded.')).toBeInTheDocument()
    expect(screen.queryByTestId('no-undecided')).toBeNull()
    expect(screen.queryByRole('button', { name: /Design again|Start a new design/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: 'Check again' })).toBeInTheDocument()
  })
})

describe('after a decision', () => {
  it('reads the list again, so the decided row is gone', async () => {
    let decided = false
    let reads = 0
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OWN]: () => {
        reads += 1
        return { operations: decided ? [] : [{ ...uncertain, job_id: JOB_ID }] }
      },
      [`POST /api/external-operations/${OPERATION_ID}/check-again`]: () => {
        decided = true
        return { ...uncertain, state: 'succeeded' }
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByText(/playlist is in your MOWL music/)).toBeInTheDocument()
    // Wait for each effect of the decision (the second read, the row gone, Design again offered)
    // instead of asserting right after the first of them: under load they land in separate ticks.
    await waitFor(() => {
      expect(reads).toBe(2)
      expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull()
    })
    expect(await screen.findByRole('button', { name: /Design again|Start a new design/ })).toBeInTheDocument()
    expect(screen.queryByTestId('no-undecided')).toBeNull() // the outcome says it already
  })

  it('drops the decided row even when the second read fails', async () => {
    let reads = 0
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OWN]: () => {
        reads += 1
        return reads === 1 ? { operations: [{ ...uncertain, job_id: JOB_ID }] } : json({}, 500)
      },
      [`POST /api/external-operations/${OPERATION_ID}/check-again`]: { ...uncertain, state: 'succeeded' },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByText(/playlist is in your MOWL music/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull()
    })
  })

  it('moves the focus into the Do it anyway confirmation', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OWN]: { operations: [{ ...uncertain, job_id: JOB_ID }] },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Do it anyway' }))
    expect(screen.getByRole('group', { name: 'Confirm' })).toHaveFocus()
  })
})

describe('the job hook', () => {
  it('a late answer never replaces a state the stream already settled', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: (() => {
        let reads = 0
        return () => {
          reads += 1
          return reads === 1 ? jobBody('running') : jobBody('cancelled')
        }
      })(),
      [`GET ${JOB}/events`]: () => sse([1, 'cancelled']),
    })
    const { result } = renderHook(() => useJobProgress(JOB_ID))
    await waitFor(() => {
      expect(result.current.job?.state).toBe('cancelled')
    })
    act(() => {
      result.current.setJob({ ...(result.current.job as NonNullable<typeof result.current.job>), state: 'running', cancelRequested: true })
    })
    expect(result.current.job?.state).toBe('cancelled')
  })

  it('a 401 means sign in again, not that the server could not be reached', async () => {
    fakeApi({ ...SESSION_ROUTES, [`GET ${JOB}`]: reason(401, 'not_authenticated') })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('heading', { name: 'You are signed out' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', `/signin?return_to=${encodeURIComponent(`/jobs/${JOB_ID}`)}`)
    expect(document.body).not.toHaveTextContent('could not be reached')
  })

  it('stopping a design that ends on its own meanwhile shows the result, not Stopping', async () => {
    let done = false
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: () => (done ? jobBody('completed') : jobBody('running')),
      [`GET ${JOB}/events`]: () => (done ? sse([1, 'completed', {}]) : openStream()),
      [`GET ${JOB}/revision`]: DRAFT,
      [`POST ${JOB}/cancel`]: () => {
        done = true
        return reason(409, 'not_cancellable')
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Stop designing' }))
    expect(await screen.findByText('This design has just finished.')).toBeInTheDocument()
    // The action is released in a later tick than its error text: wait for it.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Stop designing' })).not.toBeDisabled()
    })
  })
})

describe('Design again', () => {
  it('maps class_busy to words and keeps one key until it works', async () => {
    remember()
    const NEXT = '55555555-5555-4555-8555-555555555555'
    let posts = 0
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('failed', { error_category: 'model_unavailable' }),
      'POST /api/jobs': () => {
        posts += 1
        return posts === 1 ? reason(409, 'class_busy') : json({ ...jobBody('queued'), id: NEXT }, 201)
      },
      [`GET /api/jobs/${NEXT}`]: { ...jobBody('cancelled'), id: NEXT },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('already has a design running')
    expect(document.body).not.toHaveTextContent('class_busy')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEXT}`)
    })
    const keys = api.to('POST', '/api/jobs').map((c) => c.headers.get('Idempotency-Key'))
    expect(keys).toHaveLength(2)
    expect(keys[0]).toBe(keys[1])
    expect((api.to('POST', '/api/jobs')[1]?.body as { input: { class_id: string } }).input.class_id).toBe(CLASS_ID)
    // The attempt worked: its key and the old input are gone from storage.
    expect(window.sessionStorage.getItem(`classbeat.design-again-key.${JOB_ID}`)).toBeNull()
    expect(window.sessionStorage.getItem(`classbeat.design-input.${JOB_ID}`)).toBeNull()
  })
})

describe('Design again after MOWL was connected again', () => {
  it('drops the remembered form on not_connected and offers the form', async () => {
    remember()
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('failed', { error_category: 'model_unavailable' }),
      'POST /api/jobs': reason(409, 'not_connected'),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('connected again since')
    expect(window.sessionStorage.getItem(`classbeat.design-input.${JOB_ID}`)).toBeNull()
    expect(window.sessionStorage.getItem(`classbeat.design-again-key.${JOB_ID}`)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Start a new design' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe('/classes/new')
    })
  })
})

describe('the remembered form input', () => {
  it('is cleared on sign-out', async () => {
    remember()
    fakeApi({
      ...SESSION_ROUTES,
      'POST /api/auth/logout': { ok: true },
      [`GET ${JOB}`]: jobBody('cancelled'),
    })
    renderAt(`/jobs/${JOB_ID}`)
    await screen.findByRole('heading', { name: 'You stopped this design' })
    expect(window.sessionStorage.getItem(`classbeat.design-input.${JOB_ID}`)).not.toBeNull()
    // The focus layout has no navigation: sign out from another page of the same session.
    window.history.pushState(null, '', '/music')
    window.dispatchEvent(new PopStateEvent('popstate'))
    await userEvent.click(await screen.findByRole('button', { name: 'ann@example.test' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Sign out' }))
    await waitFor(() => {
      expect(window.sessionStorage.getItem(`classbeat.design-input.${JOB_ID}`)).toBeNull()
    })
  })

  it('is cleared when the session turns out to be gone', async () => {
    remember()
    fakeApi({ [`GET ${JOB}`]: jobBody('cancelled') }) // anonymous
    renderAt(`/jobs/${JOB_ID}`)
    await screen.findByRole('heading', { name: 'Sign in first' })
    expect(window.sessionStorage.getItem(`classbeat.design-input.${JOB_ID}`)).toBeNull()
  })

  it('is dropped when the design completes', async () => {
    remember()
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('completed'),
      [`GET ${JOB}/revision`]: DRAFT,
    })
    renderAt(`/jobs/${JOB_ID}`)
    await screen.findByRole('heading', { name: 'Your class is ready' })
    expect(window.sessionStorage.getItem(`classbeat.design-input.${JOB_ID}`)).toBeNull()
  })
})
