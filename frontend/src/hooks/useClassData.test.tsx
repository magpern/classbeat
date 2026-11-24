import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as classes from '../api/classes'
import { useClassData } from './useClassData'

vi.mock('../api/classes')

// #1519: the class summary, the revisions and the conversation are three reads. A job that ends
// between them must never leave the summary newer than the conversation: the summary then has no
// open job, so the page watches nothing and shows the request as unanswered for good.
describe('useClassData read order', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('reads the revisions and the conversation only after the summary answered', async () => {
    const calls: string[] = []
    let answerSummary: (value: classes.ClassSummary) => void = () => undefined
    vi.mocked(classes.getClass).mockImplementation(() => {
      calls.push('summary')
      return new Promise((resolve) => {
        answerSummary = resolve
      })
    })
    vi.mocked(classes.listRevisions).mockImplementation(() => {
      calls.push('revisions')
      return Promise.resolve([])
    })
    vi.mocked(classes.listMessages).mockImplementation(() => {
      calls.push('messages')
      return Promise.resolve([])
    })

    const { result } = renderHook(() => useClassData('c1'))
    await Promise.resolve()
    expect(calls).toEqual(['summary'])

    answerSummary({ id: 'c1' } as classes.ClassSummary)
    await waitFor(() => {
      expect(result.current.state.status).toBe('ready')
    })
    expect(calls).toEqual(['summary', 'revisions', 'messages'])
  })
})
