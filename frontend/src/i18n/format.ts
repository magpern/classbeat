import { clock } from '../lib/workout'
import { getActiveLocale } from './locales'
import { t } from './t'

// Dates, times, numbers, money and durations on `Intl` with the active locale and the browser's
// time zone (ADR 0023 section 4). A value that does not parse is returned as given, as before.

const formatters = new Map<string, Intl.DateTimeFormat | Intl.NumberFormat>()

function dateFormat(options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `d|${getActiveLocale()}|${JSON.stringify(options)}`
  let found = formatters.get(key)
  if (found === undefined) {
    found = new Intl.DateTimeFormat(getActiveLocale(), options)
    formatters.set(key, found)
  }
  return found as Intl.DateTimeFormat
}

function numberFormat(options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = `n|${getActiveLocale()}|${JSON.stringify(options)}`
  let found = formatters.get(key)
  if (found === undefined) {
    found = new Intl.NumberFormat(getActiveLocale(), options)
    formatters.set(key, found)
  }
  return found as Intl.NumberFormat
}

const DATE: Intl.DateTimeFormatOptions = { year: 'numeric', month: 'numeric', day: 'numeric' }
const TIME: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: 'numeric', second: 'numeric' }

function parse(iso: string): Date | null {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? null : at
}

/** A date and time of an ISO instant; `fallback` (the text as given by default) if it does not parse. */
export function dateTime(iso: string, fallback: string = iso): string {
  const at = parse(iso)
  return at === null ? fallback : dateFormat({ ...DATE, ...TIME }).format(at)
}

export function date(iso: string, fallback: string = iso): string {
  const at = parse(iso)
  return at === null ? fallback : dateFormat(DATE).format(at)
}

/** The time of day of an ISO instant; "a few minutes from now" if it does not parse. */
export function time(iso: string): string {
  const at = parse(iso)
  return at === null ? t('format.soonUnknown') : dateFormat(TIME).format(at)
}

/** `2026-10-31T12:00:00Z` as "Oct 31, 2026" (the admin area). */
export function shortDate(iso: string): string {
  const at = parse(iso)
  return at === null ? iso : dateFormat({ year: 'numeric', month: 'short', day: 'numeric' }).format(at)
}

/** As `shortDate`, with the hour and minute. */
export function shortDateTime(iso: string): string {
  const at = parse(iso)
  if (at === null) return iso
  return dateFormat({ year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(at)
}

/** `2026-10` as "October 2026" (calendar month in UTC, as the usage periods are); as given if not a month. */
export function month(value: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(value)
  if (match === null) return value
  const at = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1))
  return dateFormat({ month: 'long', year: 'numeric', timeZone: 'UTC' }).format(at)
}

/** A whole number with the locale's grouping. */
export function integer(value: number): string {
  return numberFormat({}).format(value)
}

/** A fixed number of decimals, no grouping (it replaces `toFixed`). */
export function decimal(value: number, digits: number): string {
  return numberFormat({ minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false }).format(value)
}

/** A number without trailing zeros and no grouping (it replaces `String(value)` for display). */
export function plain(value: number): string {
  return numberFormat({ maximumFractionDigits: 20, useGrouping: false }).format(value)
}

/** Dollars and cents, always USD (no grouping, as before). */
export function usd(dollars: number): string {
  return numberFormat({ style: 'currency', currency: 'USD', useGrouping: false }).format(dollars)
}

/** Micro-dollars as USD for a person: "under $0.01" for a fraction of a cent. */
export function usdMicros(micros: number): string {
  if (micros <= 0) return usd(0)
  if (micros < 10_000) return t('usage.underOneCent', { amount: usd(0.01) })
  return usd(micros / 1_000_000)
}

/** Seconds as m:ss. */
export function duration(seconds: number): string {
  return clock(seconds)
}
