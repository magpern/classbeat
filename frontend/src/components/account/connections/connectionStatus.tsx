import type { ReactNode } from 'react'
import type { Connection } from '../../../api/connections'
import type { CatalogueKey } from '../../../i18n/en'
import { t as translate } from '../../../i18n/t'
import type { ConnectionState } from './ConnectionTile'

export type Which = 'mowl' | 'swedebeat'

const LAST: Record<Which, Record<string, CatalogueKey>> = {
  mowl: {
    needs_reconnect: 'mowl.last.needs_reconnect',
    decrypt_failed: 'mowl.last.decrypt_failed',
    rate_limited: 'mowl.last.rate_limited',
    upstream_unavailable: 'mowl.last.upstream_unavailable',
  },
  swedebeat: {
    needs_reconnect: 'swed.last.needs_reconnect',
    decrypt_failed: 'swed.last.decrypt_failed',
    rate_limited: 'swed.last.rate_limited',
    upstream_unavailable: 'swed.last.upstream_unavailable',
  },
}

/** The plain words for an ADR 0004 section 5 category; the raw code is never shown. */
function lastProblem(provider: string, category: string): string {
  const table = provider === 'swedebeat' ? LAST.swedebeat : LAST.mowl
  const known = Object.hasOwn(table, category) ? table[category] : undefined
  return translate(known ?? (provider === 'swedebeat' ? 'swed.last.other' : 'mowl.last.other'))
}

/** What a tile shows for a stored connection (or none): its state, the pill's words and the detail line. */
export function describeConnection(
  found: Connection | null,
  purpose: string,
  words: { connected: string; again: string; none: string },
): { state: ConnectionState; pill: ReactNode; detail: ReactNode } {
  if (found === null) return { state: 'none', pill: words.none, detail: purpose }
  const pill = (text: string) => <span data-testid={`${found.provider}-status`}>{text}</span>
  if (found.status === 'connected')
    return {
      state: 'connected',
      pill: pill(words.connected),
      detail: found.account !== null ? <span data-testid={`${found.provider}-account`}>{found.account}</span> : purpose,
    }
  return {
    state: 'again',
    pill: pill(words.again),
    detail: found.lastError !== null ? lastProblem(found.provider, found.lastError) : purpose,
  }
}
