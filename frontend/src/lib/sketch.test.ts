import { describe, expect, it } from 'vitest'
import { sketchProfile } from './sketch'

const BASE = { length: 45, effort: 'moderate' as const, climbs: 3, sprints: 6, races: 0 }

describe('sketchProfile', () => {
  it('fits the class length and has one bar per effort', () => {
    const profile = sketchProfile(BASE)
    const total = profile.reduce((sum, [, sec]) => sum + sec, 0)
    expect(Math.abs(total - 45 * 60)).toBeLessThan(profile.length)
    expect(profile.filter(([zone]) => zone === 6)).toHaveLength(6)
    expect(profile.every(([zone]) => zone >= 1 && zone <= 7)).toBe(true)
  })

  it('is empty when a number is not usable', () => {
    expect(sketchProfile({ ...BASE, length: Number.NaN })).toEqual([])
    expect(sketchProfile({ ...BASE, length: 5 })).toEqual([])
    expect(sketchProfile({ ...BASE, climbs: Number.NaN })).toEqual([])
  })

  it('works harder for hard than for easy', () => {
    const top = (effort: 'easy' | 'hard') =>
      Math.max(...sketchProfile({ ...BASE, effort, sprints: 0 }).map(([zone]) => zone))
    expect(top('hard')).toBeGreaterThan(top('easy'))
  })
})
