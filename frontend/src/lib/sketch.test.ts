import { describe, expect, it } from 'vitest'
import { MAX_LEVEL, sketchProfile } from './sketch'

const BASE = { length: 45, effort: 'moderate' as const, climbs: 3, sprints: 6, races: 0 }

describe('sketchProfile', () => {
  it('fits the class length and has one top bar per sprint', () => {
    const profile = sketchProfile(BASE)
    const total = profile.reduce((sum, [, sec]) => sum + sec, 0)
    expect(Math.abs(total - 45 * 60)).toBeLessThan(profile.length)
    expect(profile.filter(([level, sec]) => level === MAX_LEVEL && sec < 60)).toHaveLength(6)
    expect(profile.every(([level]) => level >= 1 && level <= MAX_LEVEL)).toBe(true)
  })

  it('is empty when a number is not usable', () => {
    expect(sketchProfile({ ...BASE, length: Number.NaN })).toEqual([])
    expect(sketchProfile({ ...BASE, length: 5 })).toEqual([])
    expect(sketchProfile({ ...BASE, climbs: Number.NaN })).toEqual([])
  })

  it('sits higher for hard than for easy', () => {
    const steady = (effort: 'easy' | 'hard') =>
      Math.max(...sketchProfile({ ...BASE, effort, climbs: 0, sprints: 0 }).map(([level]) => level))
    expect(steady('hard')).toBeGreaterThan(steady('easy'))
  })
})
