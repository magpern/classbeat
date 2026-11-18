import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { askedFor, type TimelineEntry } from './classHistory'
import { ORIGIN_LABELS, originLabel } from './classMessages'

// Every revision origin the server classifies (#1186): the backend holds the fixture equal to its
// classified origins, so a new origin fails here until both views below say how they show it.
const ORIGINS = JSON.parse(
  readFileSync(join(import.meta.dirname, '../../../backend/tests/fixtures/revision_origins.json'), 'utf8'),
) as string[]

const LABEL: Record<string, string> = {
  design_draft: 'First design',
  revise_draft: 'Changed on request',
  sketch: 'Sketch',
  design_with_songs: 'Version',
  revise_songs: 'Version',
  studio_songs: 'Version',
  duplicate: 'Version',
  beat_one: 'Version',
  swedebeat_match: 'Version',
}

// What the history says asked for a version of each origin, without a request and with one.
const ASKED: Record<string, [string, string]> = {
  design_draft: ['First design', 'First design'],
  revise_draft: ['Changed on request', 'You asked: “Easier”'],
  sketch: ['First design', 'You asked: “Easier”'],
  design_with_songs: ['First design', 'First design'],
  revise_songs: ['Songs changed', 'Songs changed'],
  studio_songs: ['First design', 'First design'],
  duplicate: ['Duplicated from a class that is gone', 'Duplicated from a class that is gone'],
  beat_one: ['Beat 1 adjusted', 'Beat 1 adjusted'],
  swedebeat_match: ['First design', 'First design'],
}

function entry(origin: string, request: string | null): TimelineEntry {
  return {
    id: 'r1',
    number: 1,
    parentNumber: null,
    origin,
    createdAt: '2026-11-03T10:00:00Z',
    isCurrent: false,
    contentHash: 'ab'.repeat(32),
    segmentCount: 4,
    intervalCount: 8,
    totalSec: 2700,
    tss: 52.5,
    tssApproximate: false,
    request,
    saved: 'none',
    fromSongs: false,
    duplicatedFrom: null,
  }
}

describe('revision origins', () => {
  it('classifies exactly the origins the server stores, in both views', () => {
    expect(Object.keys(ORIGIN_LABELS).sort()).toEqual(ORIGINS)
    expect(Object.keys(LABEL).sort()).toEqual(ORIGINS)
    expect(Object.keys(ASKED).sort()).toEqual(ORIGINS)
  })

  it.each(ORIGINS)('labels %s in the revisions list and the revision view', (origin) => {
    expect(originLabel(origin)).toBe(LABEL[origin])
  })

  it.each(ORIGINS)('says what asked for a %s version in the history', (origin) => {
    const [plain, asked] = ASKED[origin] ?? ['', '']
    expect(askedFor(entry(origin, null))).toBe(plain)
    expect(askedFor(entry(origin, 'Easier'))).toBe(asked)
  })

  it('reads a revision with no origin or an unknown one as a version', () => {
    expect(originLabel('')).toBe('Version')
    expect(originLabel('brand_new')).toBe('Version')
    expect(originLabel('toString')).toBe('Version')
  })
})
