import { describe, expect, it, vi } from 'vitest'
import {
  date,
  dateTime,
  decimal,
  duration,
  integer,
  month,
  plain,
  shortDate,
  shortDateTime,
  time,
  usd,
  usdMicros,
} from './format'

// In English every formatter gives what the code it replaced gave.
const ISO = '2026-10-31T12:34:56Z'
const AT = new Date(ISO)

describe('format (English)', () => {
  it('dates and times match toLocale*', () => {
    // No seconds, and a named month so a date cannot be read two ways (UI review G8).
    expect(dateTime(ISO)).toBe(
      AT.toLocaleString('en', { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: 'numeric' }),
    )
    expect(date(ISO)).toBe(AT.toLocaleDateString('en', { year: 'numeric', month: 'short', day: 'numeric' }))
    expect(time(ISO)).toBe(AT.toLocaleTimeString('en', { hour: 'numeric', minute: 'numeric' }))
    expect(dateTime(ISO)).not.toMatch(/:\d\d:\d\d/)
    expect(shortDate(ISO)).toBe(AT.toLocaleDateString('en', { year: 'numeric', month: 'short', day: 'numeric' }))
    expect(shortDateTime(ISO)).toBe(
      AT.toLocaleString('en', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
    )
  })

  it('a value that does not parse is returned as before', () => {
    expect(dateTime('soon')).toBe('soon')
    expect(dateTime('soon', '')).toBe('')
    expect(date('soon')).toBe('soon')
    expect(time('soon')).toBe('a few minutes from now')
    expect(shortDate('soon')).toBe('soon')
    expect(month('soon')).toBe('soon')
  })

  it('months are UTC calendar months', () => {
    expect(month('2026-10')).toBe('October 2026')
    expect(month('2026-01')).toBe('January 2026')
  })

  it('a month does not move with the time zone', async () => {
    // 2026-10-01T00:00Z is still 30 September in Los Angeles: without `timeZone: 'UTC'` this reads September.
    // A fresh module, because formatters are cached with the zone they were made in.
    const before = process.env.TZ
    process.env.TZ = 'America/Los_Angeles'
    vi.resetModules()
    try {
      expect(new Date(Date.UTC(2026, 9, 1)).getMonth()).toBe(8)
      const fresh = await import('./format')
      expect(fresh.month('2026-10')).toBe('October 2026')
    } finally {
      if (before === undefined) delete process.env.TZ
      else process.env.TZ = before
      vi.resetModules()
    }
  })

  it('numbers match the old toFixed and en-US grouping', () => {
    expect(integer(1234567)).toBe('1,234,567')
    for (const value of [0, 1, 1.5, 12.34, 999999.99]) {
      expect(decimal(value, 2)).toBe(value.toFixed(2))
      expect(decimal(value, 1)).toBe(value.toFixed(1))
    }
    expect(plain(3)).toBe('3')
    expect(plain(3.25)).toBe('3.25')
    expect(plain(-0)).toBe('0')
    expect(plain(-2.5)).toBe('-2.5')
  })

  it('money is USD', () => {
    expect(usd(1.5)).toBe('$1.50')
    expect(usd(1234.5)).toBe('$1234.50')
    // Intl rounds the decimal value half-up; toFixed rounded the binary value (1.005 gave 1.00, 2.675 gave 2.67).
    expect(usd(1.005)).toBe('$1.01')
    expect(usd(2.675)).toBe('$2.68')
    expect(usdMicros(0)).toBe('$0.00')
    expect(usdMicros(-5)).toBe('$0.00')
    expect(usdMicros(9_999)).toBe('under $0.01')
    expect(usdMicros(10_000)).toBe('$0.01')
    expect(usdMicros(2_345_000)).toBe('$2.35')
  })

  it('durations are m:ss', () => {
    expect(duration(0)).toBe('0:00')
    expect(duration(3725)).toBe('62:05')
  })
})

describe('money', () => {
  it('formats an integer in the minor unit with its currency', async () => {
    const { money } = await import('./format')
    expect(money(900, 'USD')).toBe('$9.00')
    expect(money(1234, 'USD')).toBe('$12.34')
    expect(money(0, 'USD')).toBe('$0.00')
  })
})
