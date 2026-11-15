import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
import { failureText } from './jobMessages'
import { decodeJob } from './jobs'
import { playerFailureText, playerReasonText } from './playerMessages'
import da from '../i18n/da'
import { en } from '../i18n/en'
import sv from '../i18n/sv'

// What #1061 adds to the job decoding and the failure copy (ADR 0034 section 5): a job with no
// class, which a `player_remove` is, and the text of the two categories the player can refuse a
// send with.

const BASE = { id: 'j1', kind: 'player_remove', state: 'queued', cancel_requested: false }

describe('decodeJob', () => {
  it('accepts a job with no class_id (a player_remove has none)', () => {
    const job = decodeJob({ ...BASE, class_id: null })
    expect(job.kind).toBe('player_remove')
    expect(job.classId).toBe('')
  })

  it('accepts a job whose answer leaves class_id out', () => {
    expect(decodeJob(BASE).classId).toBe('')
  })

  it('still reads the class of every other job', () => {
    expect(decodeJob({ ...BASE, kind: 'send_to_player', class_id: 'c1' }).classId).toBe('c1')
  })

  it('still refuses a class_id that is not text', () => {
    expect(() => decodeJob({ ...BASE, class_id: 7 })).toThrow()
  })
})

describe('the player failure categories', () => {
  it.each(['player_ahead', 'player_conflict'])('%s has its own sentence, not the fallback', (category) => {
    const found = failureText(category)
    expect(found.title).not.toBe(en['job.failure.fallback.title'])
    expect(found.detail).not.toBe(en['job.failure.fallback.detail'])
    expect(found.title).not.toContain(category)
    expect(found.detail).not.toContain(category)
  })

  it('says player ahead means pressing again', () => {
    expect(failureText('player_ahead')).toEqual({
      title: 'The player already had a newer copy',
      detail: 'The player was ahead. We caught up, so press Send to player again.',
    })
  })

  it('says a conflict replaced nothing', () => {
    expect(failureText('player_conflict').detail).toMatch(/Nothing was replaced/)
  })

  it.each([
    ['sv', sv],
    ['da', da],
  ] as const)('has both sentences in %s, different from English', (_lang, catalogue) => {
    for (const category of ['player_ahead', 'player_conflict']) {
      for (const part of ['title', 'detail'] as const) {
        const key = `job.failure.${category}.${part}` as 'job.failure.player_ahead.title'
        expect(catalogue[key]).toBeTypeOf('string')
        expect(catalogue[key]).not.toBe(en[key])
      }
    }
  })

  it('joins title and detail for the outcome notice', () => {
    expect(playerFailureText('player_conflict')).toBe(
      'This class changed on the player. Nothing was replaced. Send to player again to replace the player’s copy with this version.',
    )
  })
})

describe('the refusals of a send', () => {
  const refused = (why: string) => new ApiError(409, why, null, undefined)

  it('words a format refusal with one sentence, whatever its code', () => {
    expect(playerReasonText(refused('cue_too_long'))).toBe(playerReasonText(refused('song_window')))
    expect(playerReasonText(refused('cue_too_long'))).toMatch(/cannot show this class as it is/)
  })

  it('keeps no_songs apart, since the user can fix it', () => {
    expect(playerReasonText(refused('no_songs'))).toMatch(/Pick songs for it first/)
  })
})
