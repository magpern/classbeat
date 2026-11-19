import { renderToString } from 'react-dom/server'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { RouterProvider } from '../../router/Router'
import { LandingHero } from './LandingHero'

// Issue 1270: the phone rules in imagery.css find the hero's title and lead by class, and the
// hero is rendered at build time (ADR 0030): no window, no document, no style attribute.
const saved = new Map<string, PropertyDescriptor | undefined>()

describe('LandingHero', () => {
  beforeAll(() => {
    for (const name of ['window', 'document']) {
      saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
      Reflect.deleteProperty(globalThis, name)
    }
  })
  afterAll(() => {
    for (const [name, descriptor] of saved) if (descriptor) Object.defineProperty(globalThis, name, descriptor)
  })

  it('renders without a window, with the classes the phone rules need and the one sign-up link', () => {
    const out = renderToString(
      <I18nProvider initial="en">
        <RouterProvider initialLocation={{ pathname: '/en', search: '' }}>
          <LandingHero />
        </RouterProvider>
      </I18nProvider>,
    )
    expect(out).not.toContain('style=')
    for (const cls of ['hero__text', 'hero__title', 'hero__lead', 'badge']) expect(out).toContain(cls)
    expect(out.match(/href="[^"]*signup"/g)).toHaveLength(1)
  })
})
