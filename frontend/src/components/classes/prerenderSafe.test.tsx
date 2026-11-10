import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderToString } from 'react-dom/server'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import type { DraftSegment } from '../../api/draftTypes'
import { I18nProvider } from '../../i18n/I18n'
import { useLayout } from '../../hooks/useLayout'
import { RouterProvider, useRouter } from '../../router/Router'
import { resolvedTheme } from '../../theme/theme'
import { CookieNotice } from '../CookieNotice'
import { SlotPicture } from '../imagery/SlotPicture'
import { MiniProfile } from './MiniProfile'
import { ProfileChart } from './ProfileChart'
import { ZoneTime } from './ZoneTime'
import { flatten } from '../../lib/profile'

// ADR 0030: the public pages are rendered at build time, with no window and no `style="…"` (the
// CSP refuses it). `window` and `document` are removed for these tests, so a touch of them throws.

const interval = (sec: number, zone: number, standing = false) => ({
  sec,
  zone,
  rpm: [85, 95] as [number, number],
  standing,
  cue: '',
})
const segments: DraftSegment[] = [
  { name: 'Main', kind: 'main', intervals: [interval(300, 2), interval(240, 5, true), interval(120, 7, true)] },
]

const html = (ui: React.ReactNode, path = '/en') =>
  renderToString(
    <I18nProvider initial="en">
      <RouterProvider initialLocation={{ pathname: path, search: '' }}>{ui}</RouterProvider>
    </I18nProvider>,
  )

const saved = new Map<string, PropertyDescriptor | undefined>()

describe('prerender-safe components', () => {
  beforeAll(() => {
    for (const name of ['window', 'document']) {
      saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
      Reflect.deleteProperty(globalThis, name)
    }
  })
  afterAll(() => {
    for (const [name, descriptor] of saved) if (descriptor) Object.defineProperty(globalThis, name, descriptor)
  })
  it('render the charts without a window and without a style attribute', () => {
    const bars = flatten(segments)
    expect(bars.length).toBeGreaterThan(0)
    for (const out of [
      html(<MiniProfile profile={[[2, 60], [5, 120], [7, 30]]} tall />),
      html(<ProfileChart segments={segments} title="Climb" songs={null} />),
      html(<ZoneTime bars={bars} />),
    ]) {
      expect(out).not.toContain('style=')
      expect(out).toContain('<svg')
    }
    expect(html(<ProfileChart segments={segments} title="Climb" songs={null} />)).toContain('stop-color:var(--cb-zone-2)')
  })

  it('size the zone-time bar by an SVG width', () => {
    const out = html(<ZoneTime bars={flatten(segments)} />)
    expect(out).toMatch(/<rect width="100%" height="12" rx="6" class="fill-zone-2"/)
  })

  it('make Router start at the given location', () => {
    function Where() {
      const { lang, path } = useRouter()
      return <p>{`${String(lang)}:${path}`}</p>
    }
    expect(html(<Where />, '/sv/how-it-works')).toContain('sv:/how-it-works')
  })

  it('are safe without a window: layout, theme, cookie notice and pictures', () => {
    function Layout() {
      return <p>{useLayout()}</p>
    }
    expect(html(<Layout />)).toContain('phone')
    expect(typeof window).toBe('undefined')
    expect(typeof document).toBe('undefined')
    expect(resolvedTheme()).toBe('dark')
    expect(html(<CookieNotice />)).not.toContain('cookie-notice')
    expect(() => html(<SlotPicture slot="hero" sizes="100vw" />)).not.toThrow()
  })

  it('import no runtime code from an API client module', () => {
    for (const file of ['ProfileChart', 'MiniProfile', 'ZoneTime']) {
      const source = readFileSync(join(process.cwd(), 'src/components/classes', `${file}.tsx`), 'utf8')
      const runtime = [...source.matchAll(/^import (?!type )[^\n]*from '\.\.\/\.\.\/api\/([\w]+)'/gm)].map((m) => m[1])
      expect(runtime.filter((name) => name !== 'draftTypes'), file).toEqual([])
    }
  })
})
