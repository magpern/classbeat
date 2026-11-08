import { describe, expect, it } from 'vitest'
import { foldEvent, type Progress } from './useJobProgress'

const WAITING: Progress = {
  stage: null,
  toolCalls: 0,
  attempt: 0,
  retry: null,
  operation: null,
  cancelRequested: false,
  waitingForSlot: true,
}

describe('foldEvent and the mix-slot wait (#572)', () => {
  it('claimed clears waitingForSlot on its own', () => {
    expect(foldEvent(WAITING, { seq: 3, kind: 'claimed', payload: { attempt: 2 } })).toMatchObject({
      waitingForSlot: false,
      attempt: 2,
    })
  })

  it('requeued sets it only for the analysis_slot reason', () => {
    const idle = { ...WAITING, waitingForSlot: false }
    expect(foldEvent(idle, { seq: 2, kind: 'requeued', payload: { reason: 'analysis_slot' } }).waitingForSlot).toBe(true)
    expect(foldEvent(idle, { seq: 2, kind: 'requeued', payload: {} }).waitingForSlot).toBe(false)
  })
})
