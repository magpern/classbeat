import { describe, expect, it } from 'vitest'
import { decodeClassPlayer, decodeDeleted } from './classes'
import { DecodeError } from './decode'

describe('decodeClassPlayer', () => {
  it('reads the sent version', () => {
    expect(decodeClassPlayer({ sent_revision_id: 'r1', sent_revision_number: 2 })).toEqual({
      sentRevisionId: 'r1',
      sentRevisionNumber: 2,
    })
  })

  it('is null when the class was never sent', () => {
    expect(decodeClassPlayer(null)).toBeNull()
    expect(decodeClassPlayer(undefined)).toBeNull()
    expect(decodeClassPlayer([])).toBeNull()
  })

  it.each([
    ['a missing revision id', { sent_revision_number: 1 }],
    ['a revision id that is not text', { sent_revision_id: 5, sent_revision_number: 1 }],
    ['a missing number', { sent_revision_id: 'r1' }],
    ['a number that is text', { sent_revision_id: 'r1', sent_revision_number: '1' }],
  ])('throws on %s', (_name, value) => {
    expect(() => decodeClassPlayer(value)).toThrow(DecodeError)
  })
})

describe('decodeDeleted', () => {
  it('reads the removal job when one started', () => {
    expect(
      decodeDeleted({ mowl_workouts_kept: 1, player_removal_started: true, player_removal_job_id: 'j1' }),
    ).toEqual({ mowlWorkoutsKept: 1, playerRemovalJobId: 'j1' })
  })

  it('has no job when none started, even if an id is present', () => {
    expect(
      decodeDeleted({ mowl_workouts_kept: 0, player_removal_started: false, player_removal_job_id: 'j1' }),
    ).toEqual({ mowlWorkoutsKept: 0, playerRemovalJobId: null })
    expect(decodeDeleted({ mowl_workouts_kept: 0 }).playerRemovalJobId).toBeNull()
  })

  it.each([
    ['a body that is not an object', 'x'],
    ['a null body', null],
    ['a missing count', { player_removal_started: false }],
    ['a count that is text', { mowl_workouts_kept: 'many' }],
    ['a job id that is not text', { mowl_workouts_kept: 0, player_removal_started: true, player_removal_job_id: 7 }],
    ['a started flag that is not a boolean', { mowl_workouts_kept: 0, player_removal_started: 'yes' }],
  ])('throws on %s', (_name, value) => {
    expect(() => decodeDeleted(value)).toThrow(DecodeError)
  })
})
