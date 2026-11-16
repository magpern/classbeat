import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
import { decodeStudioList, refusalChanges, studioError } from './studioSongs'

describe('studio songs api', () => {
  it('reads limit and count of a revision_limit refusal', () => {
    const error = new ApiError(409, 'revision_limit', null, { detail: { reason: 'revision_limit', limit: 4, count: 4 } })
    expect(refusalChanges(error)).toEqual({ count: 4, limit: 4 })
    expect(refusalChanges(new ApiError(409, 'quota', null, undefined))).toBeNull()
    expect(studioError(new ApiError(409, 'revision_limit', null, undefined))).toContain('limit of song changes')
  })

  it('falls back to a plain sentence for an unknown reason', () => {
    expect(studioError(new ApiError(500, 'weird_code', null, undefined))).not.toContain('weird_code')
  })

  it('decodes a view without changes as null', () => {
    const list = decodeStudioList({
      revision_id: 'r', content_hash: 'h', target_sec: 1, total_sec: 1, approvable: true, note: '',
      evidence: { state: 'ok', sent: 1, missing: 0, anyway: false }, songs: [],
    })
    expect(list.changes).toBeNull()
    expect(list.approvable).toBe(true)
  })
})
