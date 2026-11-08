import { describe, expect, it } from 'vitest'
import { hasUnknownLanguage, isAdminPath, localize, neutralize, prefixedTarget, splitPath, withLanguage } from './paths'
import { safeReturnTo } from './Router'

describe('splitPath', () => {
  it.each([
    ['/', null, '/'],
    ['/classes', null, '/classes'],
    ['/sv', 'sv', '/'],
    ['/sv/', 'sv', '/'],
    ['/sv/classes/new', 'sv', '/classes/new'],
    ['/da/privacy/', 'da', '/privacy'],
    ['/en/admin', 'en', '/admin'],
    ['/fr/privacy', null, '/fr/privacy'],
    ['/svenska', null, '/svenska'],
    ['/SV/privacy', null, '/SV/privacy'],
    ['//evil.example', null, '//evil.example'],
  ])('%s is %s + %s', (pathname, lang, path) => {
    expect(splitPath(pathname)).toEqual({ lang, path })
  })
})

describe('localize', () => {
  it('adds the prefix to a neutral path and keeps search and fragment', () => {
    expect(localize('/', 'sv')).toBe('/sv')
    expect(localize('/classes', 'sv')).toBe('/sv/classes')
    expect(localize('/signin?return_to=%2Fjobs%2F1#x', 'da')).toBe('/da/signin?return_to=%2Fjobs%2F1#x')
  })

  // The check is on the PATHNAME only (ADR 0027 section 4): a query or fragment after a bare code
  // must not make it look unprefixed.
  it.each(['/sv', '/sv?x=1', '/sv#y', '/sv?x=1#y', '/sv/', '/sv/classes', '/sv/classes?x=1#y'])(
    'leaves %s alone, so it never becomes /sv/sv',
    (to) => {
      expect(localize(to, 'sv')).toBe(to)
      expect(localize(to, 'da')).toBe(to)
    },
  )

  it('does not mistake a longer first segment for a code', () => {
    expect(localize('/svenska', 'sv')).toBe('/sv/svenska')
    expect(localize('/classes?lang=/sv/x', 'da')).toBe('/da/classes?lang=/sv/x')
  })

  it.each(['/admin', '/admin/users?x=1', '/api/auth/session', '/oauth/google/callback'])('leaves %s unprefixed', (to) => {
    expect(localize(to, 'sv')).toBe(to)
  })

  it('keeps a hash-only or query-only target on the current language home', () => {
    expect(localize('?a=1', 'sv')).toBe('/sv?a=1')
    expect(localize('#x', 'sv')).toBe('/sv#x')
  })

  it('stays a path on this origin whatever it is given', () => {
    for (const to of ['//evil.example', '/\\evil.example', '/%2F%2Fevil.example', 'https://evil.example/x', 'evil.example']) {
      const out = localize(to, 'sv')
      expect(out.startsWith('/sv/')).toBe(true)
      expect(new URL(out, 'https://classbeat.example').origin).toBe('https://classbeat.example')
    }
  })
})

describe('neutralize and return_to', () => {
  it('removes a prefix and keeps the rest', () => {
    expect(neutralize('/sv/jobs/1?a=b#c')).toBe('/jobs/1?a=b#c')
    expect(neutralize('/sv')).toBe('/')
    expect(neutralize('/sv?x=1')).toBe('/?x=1')
    expect(neutralize('/jobs/1?x=/sv/y')).toBe('/jobs/1?x=/sv/y')
  })

  it('makes return_to language-neutral and still refuses what is not a plain path', () => {
    expect(safeReturnTo('/sv/jobs/1')).toBe('/jobs/1')
    expect(safeReturnTo('/jobs/1')).toBe('/jobs/1')
    expect(safeReturnTo('//evil.example')).toBeNull()
    expect(safeReturnTo('/sv//evil.example')).toBe('/sv//evil.example')
    expect(neutralize('/sv//evil.example')).toBe('/sv//evil.example')
  })
})

describe('prefixedTarget (the redirect of an unprefixed URL)', () => {
  it('copies path, search and fragment', () => {
    expect(prefixedTarget('sv', '/reset', '', '#token=x')).toBe('/sv/reset#token=x')
    expect(prefixedTarget('da', '/account', '?oauth=reauthed', '')).toBe('/da/account?oauth=reauthed')
    expect(prefixedTarget('en', '/', '', '')).toBe('/en')
    expect(prefixedTarget('en', '/classes/', '?a=1', '#b')).toBe('/en/classes?a=1#b')
  })

  it.each(['//evil.example', '/\\evil.example', '/%2F%2Fevil.example', '///evil.example/x', '/%5Cevil.example'])(
    'never leaves the origin for %s',
    (pathname) => {
      const out = prefixedTarget('sv', pathname, '?next=//evil.example', '#//evil.example')
      expect(out.startsWith('/sv/')).toBe(true)
      expect(new URL(out, 'https://classbeat.example').origin).toBe('https://classbeat.example')
    },
  )
})

describe('hasUnknownLanguage and isAdminPath', () => {
  it('flags only a two-letter lowercase first segment that is not a language', () => {
    expect(hasUnknownLanguage('/fr')).toBe(true)
    expect(hasUnknownLanguage('/fr/privacy')).toBe(true)
    expect(hasUnknownLanguage('/sv/privacy')).toBe(false)
    expect(hasUnknownLanguage('/privacy')).toBe(false)
    expect(hasUnknownLanguage('/')).toBe(false)
    expect(hasUnknownLanguage('/abc')).toBe(false)
    expect(hasUnknownLanguage('/FR')).toBe(false)
  })

  it('knows the admin area', () => {
    expect(isAdminPath('/admin')).toBe(true)
    expect(isAdminPath('/admin/users')).toBe(true)
    expect(isAdminPath('/administer')).toBe(false)
    expect(withLanguage('sv', '/')).toBe('/sv')
  })
})
