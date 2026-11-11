import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render } from '@testing-library/react'
import { renderToString } from 'react-dom/server'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { SlotPicture } from './SlotPicture'

// The prerender does not know the saved theme, and a browser fetches images before any script runs
// (takeover gate, PP5). So the prerendered picture holds both themes' files, lazy, and the style
// sheet shows one: a hidden lazy image is never requested.

const wrap = (ui: React.ReactNode) => <I18nProvider initial="en">{ui}</I18nProvider>

describe('SlotPicture in the prerender', () => {
  const saved = new Map<string, PropertyDescriptor | undefined>()
  let html = ''
  beforeAll(() => {
    for (const name of ['window', 'document']) {
      saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
      Reflect.deleteProperty(globalThis, name)
    }
    html = renderToString(wrap(<SlotPicture slot="hero" priority sizes="100vw" />))
  })
  afterAll(() => {
    for (const [name, descriptor] of saved) if (descriptor) Object.defineProperty(globalThis, name, descriptor)
  })

  it('writes both themes, each marked, and no style attribute', () => {
    expect(html).toContain('data-for="dark"')
    expect(html).toContain('data-for="light"')
    expect(html).toContain('hero-dark-1600')
    expect(html).toContain('hero-light-1600')
    expect(html).not.toMatch(/\sstyle=/)
  })

  it('loads every picture lazily, so a hidden one is never requested', () => {
    const images = html.match(/<img\b[^>]*>/g) ?? []
    expect(images).toHaveLength(2)
    for (const image of images) expect(image).toContain('loading="lazy"')
  })
})

describe('SlotPicture in the browser', () => {
  it('shows one picture, the theme\'s, loaded as asked', () => {
    document.documentElement.setAttribute('data-theme', 'light')
    const { container } = render(wrap(<SlotPicture slot="hero" priority sizes="100vw" />))
    document.documentElement.removeAttribute('data-theme')
    expect(container.querySelectorAll('.slot')).toHaveLength(1)
    expect(container.querySelector('.slot')?.hasAttribute('data-for')).toBe(false)
    expect(container.querySelector('img')?.getAttribute('src')).toContain('hero-light')
    expect(container.querySelector('img')?.getAttribute('loading')).toBe('eager')
  })
})

describe('the style sheet', () => {
  const css = readFileSync(join(process.cwd(), 'src/styles/imagery.css'), 'utf8')
  it('hides the other theme\'s prerendered picture, for a saved theme and for system', () => {
    expect(css).toContain(":root[data-theme='light'] .slot[data-for='dark']")
    expect(css).toContain(":root:not([data-theme='light']) .slot[data-for='light']")
    expect(css).toContain("@media (prefers-color-scheme: light)")
    expect(css).toContain(":root[data-theme='system'] .slot[data-for='dark']")
    expect(css).toContain(":root[data-theme='system'] .slot[data-for='light']")
  })
})
