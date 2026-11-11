import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import type { InterpretationItem } from '../api/interpretationItems'
import { decodeCataloguePlan } from '../api/plan'
import { PLAN_CATALOGUE } from '../test/fakeApi'
import { heldNeed, isUpgradeRefusal, lowestPlanFor, nextPlan } from './planUpgrade'

const catalogue = PLAN_CATALOGUE.plans.map(decodeCataloguePlan)

function held(reasonCode: string, value: unknown = null): InterpretationItem {
  return { path: 'x', origin: 'not_as_asked', reasonCode, value, quote: null }
}

function quota(detail: Record<string, unknown>) {
  return new ApiError(409, 'quota', null, { detail: { reason: 'quota', ...detail } })
}

describe('heldNeed', () => {
  it('is null when nothing was held for the plan', () => {
    expect(heldNeed(null)).toBeNull()
    expect(heldNeed([])).toBeNull()
    expect(heldNeed([{ ...held('over_plan_length'), origin: 'explicit' }])).toBeNull()
    // Not a plan limit, and races are on every plan.
    expect(heldNeed([held('sport_not_offered'), held('plan_no_races'), held('game_under_10s')])).toBeNull()
  })

  it('reads each of the three plan reasons', () => {
    expect(heldNeed([held('over_plan_length', 60)])).toEqual({ minutes: 60, games: false, ftpTests: false })
    expect(heldNeed([held('over_plan_length')])).toEqual({ minutes: 0, games: false, ftpTests: false })
    expect(heldNeed([held('plan_no_games')])).toEqual({ minutes: null, games: true, ftpTests: false })
    expect(heldNeed([held('plan_no_ftp_tests')])).toEqual({ minutes: null, games: false, ftpTests: true })
  })
})

describe('lowestPlanFor', () => {
  it('names the lowest plan above the current one that allows it', () => {
    expect(lowestPlanFor(catalogue, 'free', { minutes: 60, games: false, ftpTests: false })?.id).toBe('instructor')
    expect(lowestPlanFor(catalogue, 'free', { minutes: 120, games: false, ftpTests: false })?.id).toBe('pro')
    expect(lowestPlanFor(catalogue, 'free', { minutes: null, games: true, ftpTests: true })?.id).toBe('instructor')
    expect(lowestPlanFor(catalogue, 'instructor', { minutes: 0, games: false, ftpTests: false })?.id).toBe('pro')
  })

  it('is null when no plan allows it, on the top plan, or while the catalogue is unknown', () => {
    expect(lowestPlanFor(catalogue, 'free', { minutes: 500, games: false, ftpTests: false })).toBeNull()
    expect(lowestPlanFor(catalogue, 'pro', { minutes: 0, games: false, ftpTests: false })).toBeNull()
    expect(lowestPlanFor(null, 'free', { minutes: 60, games: false, ftpTests: false })).toBeNull()
  })

  it('nextPlan is the plan just above', () => {
    expect(nextPlan(catalogue, 'free')?.name).toBe('Individual')
    expect(nextPlan(catalogue, 'pro')).toBeNull()
  })
})

describe('isUpgradeRefusal', () => {
  it.each([
    [{ quota: 'length', limit: 30 }, true],
    [{ quota: 'feature', feature: 'games' }, true],
    [{ quota: 'feature', feature: 'ftp_tests' }, true],
    [{ quota: 'classes', limit: 3, used: 3 }, true],
    [{}, true],
    // A bigger plan lifts none of these: races are on every plan, the cost cap and the Save rate stay.
    [{ quota: 'feature', feature: 'races' }, false],
    [{ quota: 'cost' }, false],
    [{ quota: 'saves', limit: 30, window: 'hour' }, false],
  ])('%j gives %s', (detail, expected) => {
    expect(isUpgradeRefusal(quota(detail))).toBe(expected)
  })

  it('never fires for a race between requests or any other error', () => {
    expect(isUpgradeRefusal(new ApiError(409, 'hash_mismatch', null, { detail: { reason: 'hash_mismatch' } }))).toBe(false)
    expect(isUpgradeRefusal(new ApiError(409, 'class_busy', null, undefined))).toBe(false)
    expect(isUpgradeRefusal(new ApiError(429, 'busy', null, undefined))).toBe(false)
    expect(isUpgradeRefusal(new Error('quota'))).toBe(false)
  })
})
