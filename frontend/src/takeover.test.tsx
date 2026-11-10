import { renderToString } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'
import { I18nProvider, useT } from './i18n/I18n'
import { setActiveLocale } from './i18n/locales'
import { isCatalogueLoaded, resetCatalogues } from './i18n/t'
import { awaitCatalogue } from './takeover'

afterEach(() => {
  resetCatalogues()
  setActiveLocale('en')
})

function Title() {
  return <h1>{useT()('landing.title')}</h1>
}

function rootWith(html: string): HTMLElement {
  const root = document.createElement('div')
  root.innerHTML = html
  return root
}

describe('takeover', () => {
  it('loads the language of the URL before the first render when #root has content', async () => {
    await awaitCatalogue(rootWith('<h1>Ditt pass</h1>'), '/sv')
    expect(isCatalogueLoaded('sv')).toBe(true)
    expect(isCatalogueLoaded('da')).toBe(false)
    expect(renderToString(<I18nProvider initial="sv"><Title /></I18nProvider>)).toContain('Ditt pass, i takt med musiken.')
  })

  it('shows English on the first render when the catalogue was not awaited', () => {
    expect(renderToString(<I18nProvider initial="sv"><Title /></I18nProvider>)).toContain('Your class, timed to the music.')
  })

  it('waits for nothing when #root is empty', async () => {
    await awaitCatalogue(rootWith(''), '/da/privacy')
    expect(isCatalogueLoaded('da')).toBe(false)
  })

  it('needs no load for English', async () => {
    await awaitCatalogue(rootWith('<h1>x</h1>'), '/en/terms')
    expect(isCatalogueLoaded('sv')).toBe(false)
    expect(isCatalogueLoaded('en')).toBe(true)
  })
})
