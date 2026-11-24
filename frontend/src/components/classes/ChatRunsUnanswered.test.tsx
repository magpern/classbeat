import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as jobs from '../../api/jobs'
import type { ClassMessage } from '../../api/classes'
import { RouterProvider } from '../../router/Router'
import { JOB_ID, jobBody } from '../../test/jobFixtures'
import { Unanswered } from './ChatRuns'

vi.mock('../../api/jobs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/jobs')>()),
  getJob: vi.fn(),
}))

const MESSAGE: ClassMessage = {
  seq: 1,
  role: 'user',
  body: 'Cut the last song shorter',
  jobId: JOB_ID,
  baseRevisionId: null,
  resultRevisionId: null,
  change: null,
  createdAt: '2026-11-24T15:25:45Z',
}

function show(state: string, onReload: () => void) {
  vi.mocked(jobs.getJob).mockResolvedValue(jobs.decodeJob(jobBody(state, { kind: 'studio_revise' })))
  render(
    <RouterProvider>
      <Unanswered message={MESSAGE} latest songs="change" onChangeSongs={() => undefined} onReload={onReload} />
    </RouterProvider>,
  )
}

// #1519: a request whose job completed after the conversation was read has an answer; saying
// "did not finish. Nothing changed." there is wrong.
describe('Unanswered and a completed job', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('reads the conversation once and does not say the request failed', async () => {
    const onReload = vi.fn()
    show('completed', onReload)
    await waitFor(() => {
      expect(onReload).toHaveBeenCalledTimes(1)
    })
    expect(screen.queryByText('This request did not finish. Nothing changed.')).toBeNull()
    expect(screen.getByText('Checking what happened')).toBeInTheDocument()
  })

  it.each(['cancelled', 'running'])('does not reload for a %s job', async (state) => {
    const onReload = vi.fn()
    show(state, onReload)
    await waitFor(() => {
      expect(jobs.getJob).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(screen.queryByText('Checking what happened')).toBeNull()
    })
    expect(onReload).not.toHaveBeenCalled()
  })
})
