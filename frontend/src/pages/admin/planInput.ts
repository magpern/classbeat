// What the admin types in a plan form, read as the numbers `PATCH /api/admin/plans/{id}` takes
// (backend/src/classbeat/admin/plan_rules.py). Pure: no React, no request. The server checks
// every value again; these only catch a malformed one before anything is sent.

/** Whole dollars and cents typed by the admin as micro-dollars; null when it is not a number. */
export function micros(typed: string): number | null {
  if (!/^\d+(\.\d{1,2})?$/.test(typed.trim())) return null
  return Math.round(Number(typed.trim()) * 100) * 10_000
}

/** Price in whole cents from typed currency units ("9.90"); blank clears (null); undefined if malformed. */
export function cents(typed: string): number | null | undefined {
  if (typed.trim() === '') return null
  if (!/^\d+(\.\d{1,2})?$/.test(typed.trim())) return undefined
  return Math.round(Number(typed.trim()) * 100)
}

export function centsText(value: number | null): string {
  return value === null ? '' : (value / 100).toFixed(2)
}

/** A whole number; null when it is not one. */
export function whole(typed: string): number | null {
  return /^\d+$/.test(typed.trim()) ? Number(typed.trim()) : null
}

/**
 * A plan's own Save and Update cap (#809): blank clears it (null, so the server's setting
 * applies), a whole number from `low` to `high` sets it (the table's check), anything else is
 * undefined (malformed or out of range).
 */
export function cap(typed: string, low: number, high: number): number | null | undefined {
  if (typed.trim() === '') return null
  const value = whole(typed)
  return value !== null && value >= low && value <= high ? value : undefined
}

export function capText(value: number | null): string {
  return value === null ? '' : String(value)
}

/** The table's checks on `plans.saves_per_hour` and `plans.saves_per_day` (migration 0042). */
export const SAVES_PER_HOUR_RANGE = [1, 1000] as const
export const SAVES_PER_DAY_RANGE = [1, 10_000] as const
