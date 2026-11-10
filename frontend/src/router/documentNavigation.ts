import { LISTED_PATHS } from '../publicSite/publicPaths'
import { splitPath } from './paths'

// When a move is a real page load (ADR 0030 section 6). Pure: the router calls it with the current
// pathname, the target and whether the user is signed in, and does the load itself.
//
// The server writes the head (title, description, canonical, noindex ...) once per document, so a
// client-side move into or out of a public page would leave the previous page's head in place. A
// move is therefore a document load when either end is a listed public page (`published` or
// `draft`). The one exception is a signed-in user moving between Home and an app page: they must not
// see Landing on every return to Home, and no crawler is ever in that state.

const LISTED: ReadonlySet<string> = new Set(LISTED_PATHS)

interface Place {
  /** True for a listed page under a language prefix. */
  listed: boolean
  /** The prefix and the neutral path, to tell a different page from the same page's clean-up. */
  key: string
  home: boolean
}

function place(pathname: string): Place {
  const { lang, path } = splitPath(pathname)
  // Listed paths are written without the slash for Home (`""`), exactly as the server looks them up.
  const listed = lang !== null && LISTED.has(path === '/' ? '' : path)
  return { listed, key: `${lang ?? ''}${path}`, home: lang !== null && path === '/' }
}

/** `to` is a localized target and may carry a query and a fragment; only its pathname counts. */
export function needsDocumentLoad(from: string, to: string, authenticated: boolean): boolean {
  const cut = to.search(/[?#]/)
  const target = place(cut === -1 ? to : to.slice(0, cut))
  const current = place(from)
  // The same page again (a trailing slash, a query or a fragment only): nothing to re-author.
  if (current.key === target.key) return false
  if (!current.listed && !target.listed) return false
  if (authenticated && ((current.home && !target.listed) || (target.home && !current.listed))) {
    return false
  }
  return true
}
