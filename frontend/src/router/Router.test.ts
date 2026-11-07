import { describe, expect, it } from 'vitest'
import { safeReturnTo } from './Router'

describe('safeReturnTo', () => {
  it('keeps relative paths with query and fragment', () => {
    expect(safeReturnTo('/account')).toBe('/account')
    expect(safeReturnTo('/account?a=1&b=2#x')).toBe('/account?a=1&b=2#x')
    expect(safeReturnTo('/a%20b')).toBe('/a%20b')
  })

  it('returns a language-neutral path', () => {
    expect(safeReturnTo('/sv/classes/new')).toBe('/classes/new')
    expect(safeReturnTo('/sv/classes?x=1#y')).toBe('/classes?x=1#y')
  })

  it('treats a prefix followed by a query or fragment as the prefix alone', () => {
    expect(safeReturnTo('/sv?x=1')).toBe('/?x=1')
    expect(safeReturnTo('/sv#y')).toBe('/#y')
    expect(safeReturnTo('/sv/?x=1#y')).toBe('/?x=1#y')
    expect(safeReturnTo('/sv/admin?x=1')).toBe('/admin?x=1')
  })

  it('does not mistake a code inside the query, the fragment or a longer word for a prefix', () => {
    expect(safeReturnTo('/classes?lang=/sv/x')).toBe('/classes?lang=/sv/x')
    expect(safeReturnTo('/classes#/sv/x')).toBe('/classes#/sv/x')
    expect(safeReturnTo('/svenska?x=1')).toBe('/svenska?x=1')
  })

  it('keeps a path of two slashes after the prefix on this origin', () => {
    expect(safeReturnTo('/sv//evil.example?x=1')?.startsWith('/sv/')).toBe(true)
  })

  it.each([
    null,
    '',
    'account',
    'https://evil.example/',
    'HTTP://evil.example',
    'https://evil.example/sv/classes',
    'https://evil.example/?x=1#y',
    '/sv?next=https://evil.example',
    '/sv#https://evil.example',
    '//evil.example',
    '///evil.example',
    '/\\evil.example',
    '/\\/evil.example',
    '/ok\\path',
    'javascript:alert(1)',
    '/redirect?to=https://evil.example',
    '/a\nb',
    '/a\tb',
    '/a\u007fb',
    '/%2F%2Fevil.example',
    '/%5Cevil.example',
    '/%0d%0aSet-Cookie:x',
    '/%09x',
    '/%E0%A4%A',
    '/sv/%2F%2Fevil.example',
    '/sv/%2Fevil.example',
    '/sv/%5Cevil.example',
    '/da/%2F%2Fevil.example?x=1',
  ])('refuses %j', (value) => {
    expect(safeReturnTo(value)).toBeNull()
  })
})
