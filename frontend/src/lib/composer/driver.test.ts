import { describe, expect, it } from 'vitest'
import type { PendingMessage } from '../../api/pendingMessages'
import { nextStep, pendingOf, stepKey, type DriverInput } from './driver'

const TARGET = { revisionId: 'draft-1', contentHash: 'abc', jobId: 'phase2-1' }
const msg = (status: PendingMessage['status'], id = 'm1'): PendingMessage => ({
  messageId: id,
  body: 'make it harder',
  status,
  reason: status === 'failed' ? 'quota' : null,
  jobId: null,
  createdAt: '2026-11-18T10:00:00Z',
})

function given(over: Partial<DriverInput> = {}): DriverInput {
  return { running: false, sketchCurrent: false, fillStarted: false, savePending: null, messages: [], music: 'ready', ...over }
}

describe('the driver order (ADR 0038 section 8)', () => {
  const rows: [string, DriverInput, string][] = [
    ['a queued or running job: nothing', given({ running: true, sketchCurrent: true, savePending: TARGET, messages: [msg('pending')] }), 'none'],
    ['a sketch with no fill started: the fill', given({ sketchCurrent: true }), 'fill'],
    ['a sketch with no fill and a pending message: the message first', given({ sketchCurrent: true, messages: [msg('pending')] }), 'submit'],
    ['a sketch with a fill started and nothing else', given({ sketchCurrent: true, fillStarted: true }), 'none'],
    ['a stored fill refusal is not retried by itself', given({ sketchCurrent: true, fillStarted: true, messages: [msg('failed')] }), 'none'],
    ['save_pending: save exactly that', given({ savePending: TARGET }), 'save'],
    ['save_pending before a pending message', given({ savePending: TARGET, messages: [msg('pending')] }), 'save'],
    ['a pending message after a fill started', given({ sketchCurrent: true, fillStarted: true, messages: [msg('pending')] }), 'submit'],
    ['a pending message on a draft', given({ messages: [msg('pending')] }), 'submit'],
    ['only submitted and failed messages: nothing', given({ messages: [msg('submitted'), msg('failed', 'm2')] }), 'none'],
    ['a draft with nothing to do', given(), 'none'],
    ['a sketch while the music question is open: no fill', given({ sketchCurrent: true, music: 'undecided' }), 'none'],
    ['a sketch with no Spotify connected: no fill', given({ sketchCurrent: true, music: 'not_connected' }), 'none'],
    ['a sketch with no working-playlist consent: no fill', given({ sketchCurrent: true, music: 'consent_missing' }), 'none'],
    ['a sketch while Spotify is still being read: no fill', given({ sketchCurrent: true, music: 'checking' }), 'none'],
    ['a sketch with song picking unavailable: no fill', given({ sketchCurrent: true, music: 'unavailable' }), 'none'],
    ['a pending message still goes first while the music waits', given({ sketchCurrent: true, music: 'undecided', messages: [msg('pending')] }), 'submit'],
    ['a draft saves whatever the music says', given({ savePending: TARGET, music: 'undecided' }), 'save'],
  ]
  it.each(rows)('%s', (_name, input, kind) => {
    expect(nextStep(input).kind).toBe(kind)
  })

  it('saves the revision and hash save_pending names, never another', () => {
    const step = nextStep(given({ savePending: TARGET }))
    expect(step).toEqual({ kind: 'save', target: TARGET })
  })

  it('submits the pending message by its id', () => {
    expect(nextStep(given({ messages: [msg('failed', 'old'), msg('pending', 'new')] }))).toEqual({
      kind: 'submit',
      messageId: 'new',
    })
    expect(pendingOf([msg('submitted')])).toBeNull()
  })

  it('gives the same step and key after a reload of the same state', () => {
    const state = given({ sketchCurrent: true, messages: [msg('pending')] })
    const a = nextStep(state)
    const b = nextStep({ ...state, messages: [...state.messages] })
    expect(b).toEqual(a)
    expect(stepKey('c', a, 'r1')).toBe(stepKey('c', b, 'r1'))
  })

  it('changes the key when the sketch changes, so the new sketch gets its fill', () => {
    expect(stepKey('c', { kind: 'fill' }, 'r1')).not.toBe(stepKey('c', { kind: 'fill' }, 'r2'))
  })
})
