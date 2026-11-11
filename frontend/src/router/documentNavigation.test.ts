import { describe, expect, it } from 'vitest'
import { needsDocumentLoad } from './documentNavigation'

// from, to, signed in, a real page load?
const CASES: readonly [string, string, boolean, boolean][] = [
  // public to public, and a language switch on a public page
  ['/en', '/en/privacy', false, true],
  ['/en/privacy', '/en/terms', false, true],
  ['/en/privacy', '/sv/privacy', false, true],
  ['/en', '/sv', false, true],
  // Help is a public page
  ['/en', '/en/help', false, true],
  ['/en/help', '/en/classes', false, true],
  // public to app and app to public
  ['/en', '/en/signin', false, true],
  ['/en/privacy', '/en/signin', false, true],
  ['/en/signin', '/en', false, true],
  ['/en/classes', '/en/privacy', true, true],
  ['/en/signin', '/en/terms', false, true],
  // app to app, a language switch on an app page, and the admin area
  ['/en/classes', '/en/music', true, false],
  ['/en/classes', '/sv/classes', true, false],
  ['/en/classes', '/admin', true, false],
  ['/admin', '/en/classes', true, false],
  // the same page: a query, a fragment, a trailing slash
  ['/en/privacy', '/en/privacy?x=1', false, false],
  ['/en/privacy', '/en/privacy#top', false, false],
  ['/en/privacy/', '/en/privacy', false, false],
  ['/en', '/en?next=1#a', false, false],
  // the signed-in exception: Home and app pages stay client-side, in both directions
  ['/en', '/en/classes', true, false],
  ['/en/classes', '/en', true, false],
  ['/en/classes/new', '/en/', true, false],
  // ... but only for Home: other public pages and Home to another language still load
  ['/en', '/en/privacy', true, true],
  ['/en', '/sv', true, true],
  ['/en/privacy', '/en/classes', true, true],
  // signed out, Home and app pages are a real load
  ['/en', '/en/classes', false, true],
  ['/en/classes', '/en', false, true],
  // an unprefixed URL is an app shell; going to a prefixed public page loads it
  ['/privacy', '/en/privacy', false, true],
  ['/signin', '/en/signin', false, false],
  ['/en/xx', '/en/classes', false, false],
]

describe('needsDocumentLoad', () => {
  it.each(CASES)('%s -> %s (signed in: %s) is a page load: %s', (from, to, signedIn, load) => {
    expect(needsDocumentLoad(from, to, signedIn)).toBe(load)
  })

  it('ignores a look-alike path, a query that names a listed path and a host', () => {
    expect(needsDocumentLoad('/en/classes', '/en/classes?next=/en/privacy', false)).toBe(false)
    expect(needsDocumentLoad('/en/classes', '/en/privacy-x', false)).toBe(false)
    expect(needsDocumentLoad('/en/classes', '/en/music#/en/privacy', false)).toBe(false)
  })
})
