import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { OutputStatus } from '../../api/studioOutputStates'
import { fakeApi, json, reason } from '../../test/fakeApi'
import { StudioOutputStates } from './StudioOutputStates'

const CLASS = '11111111-1111-4111-8111-111111111111'
const REV = '22222222-2222-4222-8222-222222222222'
const BASE = `/api/classes/${CLASS}/outputs`

function status(extra: Record<string, unknown>) {
  return {
    revision_id: REV,
    output: 'swedebeat_playlist',
    attempt: 1,
    state: 'in_doubt',
    actions: ['check_again', 'create_another'],
    note: 'may_exist',
    ...extra,
  }
}

async function shown(item: Record<string, unknown>, onChange = vi.fn(), limitReached = false) {
  const { decodeOutputStatus } = await import('../../api/studioOutputStates')
  render(<StudioOutputStates classId={CLASS} outputs={[decodeOutputStatus(item)]} limitReached={limitReached} onChange={onChange} />)
  return onChange
}

describe('the per-output states', () => {
  it('words a created playlist that is not published, with Approve again as the way on', async () => {
    await shown(status({ state: 'failed', actions: ['retry'], note: 'created_not_public', name: 'My list' }))
    expect(screen.getByText(/created but not published yet/)).toBeInTheDocument()
    expect(screen.getByText('Name: My list')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('offers Check again for a state in doubt and shows the answer', async () => {
    const api = fakeApi({ [`POST ${BASE}/swedebeat_playlist/check-again`]: () => json(status({ state: 'done', actions: [], note: null })) })
    const onChange = await shown(status({}))
    expect(screen.getByText(/may exist/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    await waitFor(() => { expect(onChange).toHaveBeenCalled() })
    const call = api.to('POST', `${BASE}/swedebeat_playlist/check-again`)[0]!
    expect(call.body).toEqual({ revision_id: REV })
    expect(call.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    expect((onChange.mock.calls[0]![0] as OutputStatus).state).toBe('done')
  })

  it('offers Check again for a mix published but not listed in MOWL', async () => {
    await shown(status({ output: 'mowl', state: 'published_not_listed', actions: ['check_again'], note: null }))
    expect(screen.getByText(/not listed in MOWL yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check again' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create another anyway' })).toBeNull()
  })

  it('words writes_off and offers no action', async () => {
    await shown(status({ state: 'failed', actions: ['retry'], note: null, category: 'writes_off' }))
    expect(screen.getByText(/writes switched off/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('shows a found MOWL entry for the user to confirm', async () => {
    const api = fakeApi({ [`POST ${BASE}/swedebeat_mix/confirm`]: () => json(status({ output: 'swedebeat_mix', state: 'done', actions: [], note: null })) })
    const onChange = await shown(
      status({ output: 'swedebeat_mix', actions: ['check_again', 'confirm', 'create_another'], note: 'listed_candidate', candidate: { name: 'Mix 1', duration_sec: 605 } }),
    )
    expect(screen.getByText('Found in MOWL: Mix 1 (10:05). Is this your mix?')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Yes, it is my mix' }))
    await waitFor(() => { expect(onChange).toHaveBeenCalled() })
    expect(api.to('POST', `${BASE}/swedebeat_mix/confirm`)).toHaveLength(1)
  })

  it('states the duplicate risk and posts Create another anyway only after an explicit confirmation', async () => {
    const api = fakeApi({
      [`POST ${BASE}/swedebeat_playlist/create-another`]: () => json(status({ actions: ['retry'], superseded: true, note: null })),
    })
    const onChange = await shown(status({}))
    fireEvent.click(screen.getByRole('button', { name: 'Create another anyway' }))
    const warning = screen.getByTestId('studio-duplicate-warning')
    expect(warning).toHaveTextContent('duplicate')
    expect(warning).toHaveTextContent('no delete')
    const confirm = screen.getByRole('button', { name: 'Create another anyway', description: '' })
    expect(confirm).toBeDisabled()
    expect(api.to('POST', `${BASE}/swedebeat_playlist/create-another`)).toHaveLength(0)
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(confirm)
    await waitFor(() => { expect(onChange).toHaveBeenCalled() })
    const call = api.to('POST', `${BASE}/swedebeat_playlist/create-another`)[0]!
    expect(call.body).toEqual({ revision_id: REV, acknowledge_duplicate: true })
    expect(call.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
  })

  it('cancels without posting', async () => {
    const api = fakeApi({})
    await shown(status({}))
    fireEvent.click(screen.getByRole('button', { name: 'Create another anyway' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByTestId('studio-duplicate-warning')).toBeNull()
    expect(api.calls).toHaveLength(0)
  })

  it('words a refusal in plain text', async () => {
    fakeApi({ [`POST ${BASE}/swedebeat_playlist/check-again`]: () => reason(409, 'not_in_doubt') })
    await shown(status({}))
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    const alert = await screen.findByRole('alert')
    expect(alert).not.toHaveTextContent('not_in_doubt')
    expect(alert.textContent?.length).toBeGreaterThan(10)
  })

  it('does not say SwedeBeat has no delete for a Spotify playlist', async () => {
    await shown(status({ output: 'spotify_playlist' }))
    fireEvent.click(screen.getByRole('button', { name: 'Create another anyway' }))
    const warning = screen.getByTestId('studio-duplicate-warning')
    expect(warning).toHaveTextContent('Spotify')
    expect(warning).not.toHaveTextContent('no delete')
  })

  it('stops offering approve again once the limit is reached', async () => {
    await shown(status({ state: 'failed', actions: ['retry'], note: null, category: 'internal' }), vi.fn(), true)
    expect(screen.getByTestId('studio-output-limit')).toBeInTheDocument()
    expect(screen.queryByText(/Approve the outputs again/)).toBeNull()
  })

  it('offers no approve again where it cannot help, and says it once where it can', async () => {
    await shown(status({ state: 'failed', actions: ['retry'], note: null, category: 'render_not_reproducible' }))
    expect(screen.getByText(/came out differently/)).toBeInTheDocument()
    expect(screen.queryByText(/Approve the outputs again/)).toBeNull()
  })

  it('says approve again once for a retryable failure', async () => {
    await shown(status({ state: 'failed', actions: ['retry'], note: null, category: 'rate_limited' }))
    expect(screen.getAllByText(/Approve the outputs again/)).toHaveLength(1)
  })
})
