import { ApiError } from '../api/client'
import type { InterpretationItem } from '../api/interpretationItems'
import type { CataloguePlan } from '../api/plan'

// Which plan would lift a limit, and which refusals are worth an upgrade prompt (M7a-4). Pure: no
// network and no storage. The server stays the authority; this only words what it already said.

/** What a request held to the plan needed: a length, games, an FTP test. */
export interface Need {
  /** The minutes asked for when the reading kept them, else 0 (any plan with a longer cap). */
  minutes: number | null
  games: boolean
  ftpTests: boolean
}

/** The reason codes of an item the interpretation held to the plan ("Not as asked"). Races are on every plan. */
export const HELD_REASONS: readonly string[] = ['over_plan_length', 'plan_no_games', 'plan_no_ftp_tests']

/** What the held items asked for, or null when none of them was held for the plan. */
export function heldNeed(items: readonly InterpretationItem[] | null): Need | null {
  if (items === null) return null
  const held = items.filter((i) => i.origin === 'not_as_asked' && i.reasonCode !== null && HELD_REASONS.includes(i.reasonCode))
  if (held.length === 0) return null
  const length = held.find((i) => i.reasonCode === 'over_plan_length')
  return {
    minutes: length === undefined ? null : typeof length.value === 'number' ? length.value : 0,
    games: held.some((i) => i.reasonCode === 'plan_no_games'),
    ftpTests: held.some((i) => i.reasonCode === 'plan_no_ftp_tests'),
  }
}

/** The lowest plan above the current one that allows everything in `need`; null when none does. */
export function lowestPlanFor(
  catalogue: readonly CataloguePlan[] | null,
  currentId: string | null,
  need: Need,
): CataloguePlan | null {
  if (catalogue === null) return null
  const current = catalogue.find((p) => p.id === currentId)
  const above = catalogue.filter((p) => current === undefined || p.rank > current.rank)
  const fits = above.filter(
    (p) =>
      (!need.games || p.gates.games) &&
      (!need.ftpTests || p.gates.ftpTests) &&
      (need.minutes === null ||
        (p.limits.maxClassMinutes >= need.minutes &&
          (current === undefined || p.limits.maxClassMinutes > current.limits.maxClassMinutes))),
  )
  return fits.reduce<CataloguePlan | null>((low, p) => (low === null || p.rank < low.rank ? p : low), null)
}

/** The lowest plan above the current one at all (the gentle nudge's target). */
export function nextPlan(catalogue: readonly CataloguePlan[] | null, currentId: string | null): CataloguePlan | null {
  return lowestPlanFor(catalogue, currentId, { minutes: null, games: false, ftpTests: false })
}

/**
 * A 409 `quota` that a bigger plan would lift: the class allowance, a length or a game or FTP test.
 * Never the cost cap or the Save rate (a plan does not change those), a race (on every plan), or a
 * stale-page 409, which is a race between two requests and not a limit.
 */
export function isUpgradeRefusal(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.reason !== 'quota') return false
  const body: unknown = error.body
  const detail = typeof body === 'object' && body !== null ? (body as { detail?: unknown }).detail : undefined
  const kind = typeof detail === 'object' && detail !== null ? (detail as Record<string, unknown>).quota : undefined
  const feature = typeof detail === 'object' && detail !== null ? (detail as Record<string, unknown>).feature : undefined
  if (kind === 'feature') return feature !== 'races'
  return kind === undefined || kind === 'classes' || kind === 'length'
}
