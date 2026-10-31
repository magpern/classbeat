import { beforeEach, describe, expect, it } from 'vitest'
import {
  actionKey,
  clearSubmittedInputs,
  recallPick,
  rememberPick,
} from './submittedInput'

const BRIEF = { length_min: 45, intensity: 'hard', climbs: 0, sprints: 0, races: 0, ftp_test: null, style: '', text: 'x' } as const

describe('remembered song-pick forms', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })

  it('are kept per job and read back', () => {
    rememberPick('job-1', { brief: BRIEF, mode: 'pick', mowl_connection_id: 'm', spotify_connection_id: 's' })
    expect(recallPick('job-1')?.brief.text).toBe('x')
    expect(recallPick('job-2')).toBeNull()
  })

  it('are removed, with the action keys, when the session ends (sign-out, signed-out session)', () => {
    rememberPick('job-1', { brief: BRIEF, mode: 'pick', mowl_connection_id: 'm', spotify_connection_id: 's' })
    actionKey('resume.job-1')
    expect(window.sessionStorage.length).toBe(2)
    clearSubmittedInputs()
    expect(recallPick('job-1')).toBeNull()
    expect(window.sessionStorage.length).toBe(0)
  })
})
