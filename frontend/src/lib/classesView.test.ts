import { describe, expect, it } from 'vitest'
import type { ClassSummary } from '../api/classes'
import { classesStats, featureClass } from './classesView'

const make = (id: string, updatedAt: string, version: number | null, saved = 0): ClassSummary =>
  ({ id, updatedAt, currentRevisionNumber: version, saved: { count: saved } }) as unknown as ClassSummary

describe('featureClass', () => {
  it('is the newest class that has a version, never a draft', () => {
    const list = [
      make('draft', '2026-11-10T10:00:00Z', null),
      make('old', '2026-11-01T10:00:00Z', 1),
      make('new', '2026-11-05T10:00:00Z', 2),
    ]
    expect(featureClass(list)?.id).toBe('new')
  })
  it('keeps the earlier one in the list on a tie and is null without a version', () => {
    expect(featureClass([make('a', '2026-11-05T10:00:00Z', 1), make('b', '2026-11-05T10:00:00Z', 1)])?.id).toBe('a')
    expect(featureClass([make('a', '2026-11-05T10:00:00Z', null)])).toBeNull()
    expect(featureClass([])).toBeNull()
  })
})

describe('classesStats', () => {
  it('counts the classes and those saved to MOWL', () => {
    expect(classesStats([make('a', 'x', 1, 2), make('b', 'x', null), make('c', 'x', 1, 1)])).toEqual({ total: 3, saved: 2 })
  })
})
