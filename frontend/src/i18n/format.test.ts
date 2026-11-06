import { describe, expect, it } from 'vitest'
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
    expect(dateTime(ISO)).toBe(AT.toLocaleString('en'))
    expect(date(ISO)).toBe(AT.toLocaleDateString('en'))
    expect(time(ISO)).toBe(AT.toLocaleTimeString('en'))
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

  it('numbers match the old toFixed and en-US grouping', () => {
    expect(integer(1234567)).toBe('1,234,567')
    for (const value of [0, 1, 1.5, 12.34, 999999.99]) {
      expect(decimal(value, 2)).toBe(value.toFixed(2))
      expect(decimal(value, 1)).toBe(value.toFixed(1))
    }
    expect(plain(3)).toBe('3')
    expect(plain(3.25)).toBe('3.25')
  })

  it('money is USD', () => {
    expect(usd(1.5)).toBe('$1.50')
    expect(usd(1234.5)).toBe('$1234.50')
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
