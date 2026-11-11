import { describe, expect, it } from 'vitest'
import { decodeCataloguePlan, decodePlan } from './plan'

const LIMITS = { classes_per_month: 9, revisions_per_class: 10, concurrent_jobs: 2, max_class_minutes: 75 }
const GATES = { games: true, races: true, ftp_tests: true }

describe('the plan catalogue decoders (M7a-2)', () => {
  it('decodes a public catalogue row with its quality and no price', () => {
    const plan = decodeCataloguePlan({
      id: 'instructor', name: 'Individual', rank: 1, quality: 'better', limits: LIMITS, gates: GATES,
    })
    expect(plan).toMatchObject({ id: 'instructor', name: 'Individual', quality: 'better' })
    expect(plan.limits.maxClassMinutes).toBe(75)
    expect(Object.keys(plan)).not.toContain('price')
  })

  it('refuses a quality word it does not know', () => {
    expect(() =>
      decodeCataloguePlan({ id: 'x', name: 'X', rank: 0, quality: 'gpt-9', limits: LIMITS, gates: GATES }),
    ).toThrow()
  })

  it('decodes the account plan quality', () => {
    const plan = decodePlan({
      plan: 'pro', name: 'Professional', quality: 'best', source: 'admin', ends_at: null, limits: LIMITS,
      gates: GATES, classes_held: 0, period_end: '2026-12-01T00:00:00Z', cost_share: 0, features: [], trial: null,
    })
    expect(plan.quality).toBe('best')
    expect(plan.source).toBe('admin')
  })
})
