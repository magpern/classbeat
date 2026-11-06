import { render, screen, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import initScript from '../../public/locale-init.js?raw'
import { CATALOGUE_LOADERS } from './catalogues'
import { I18nProvider, Trans, useT } from './I18n'
import manifest from './locales.json'
import {
  DEFAULT_LOCALE,
  fromLanguages,
  getActiveLocale,
  isLocale,
  LOCALE_STORAGE_KEY,
  LOCALES,
  resolveBrowserLocale,
  setActiveLocale,
} from './locales'
import { loadCatalogue, plural, resetCatalogues, t, translate } from './t'

const loaders = { xx: () => import('./testing/xx') }

function languages(list: string[]) {
  vi.spyOn(window.navigator, 'languages', 'get').mockReturnValue(list)
}

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.setAttribute('lang', 'en')
})
afterEach(() => {
  vi.restoreAllMocks()
  resetCatalogues()
  setActiveLocale(DEFAULT_LOCALE)
})

describe('the manifest', () => {
  it('is English only until ML7', () => {
    expect(manifest).toEqual(['en'])
    expect([...LOCALES]).toEqual(manifest)
  })

  it('has one static loader per non-English locale and no other', () => {
    expect(Object.keys(CATALOGUE_LOADERS).sort()).toEqual(manifest.filter((l) => l !== 'en').sort())
  })

  it('is the list in public/locale-init.js', () => {
    const list = /var locales = (\[[^\]]*\])/.exec(initScript)?.[1]
    expect(list).toBeDefined()
    expect(JSON.parse((list ?? '').replaceAll("'", '"'))).toEqual(manifest)
  })

  it('only accepts its own codes', () => {
    expect(isLocale('en')).toBe(true)
    for (const bad of ['sv', 'xx', 'EN', '../en', '', null, 3]) expect(isLocale(bad)).toBe(false)
  })
})

describe('browser resolution', () => {
  it('is English with nothing saved', () => {
    languages([])
    expect(resolveBrowserLocale()).toBe('en')
  })

  it('ignores a browser that prefers a language the manifest lacks', () => {
    languages(['sv-SE', 'da', 'en-GB'])
    expect(fromLanguages(['sv-SE', 'da'])).toBe('en')
    expect(resolveBrowserLocale()).toBe('en')
  })

  it('takes the first manifest language', () => {
    expect(fromLanguages(['fr', 'en-GB', 'sv'])).toBe('en')
  })

  it('ignores a saved value outside the manifest', () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'sv')
    languages(['en-US'])
    expect(resolveBrowserLocale()).toBe('en')
  })

  it('survives blocked storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    languages(['en'])
    expect(resolveBrowserLocale()).toBe('en')
  })
})

describe('locale-init.js (runs before first paint)', () => {
  function run() {
    window.eval(initScript)
  }

  it('gives a browser preferring sv lang="en" while the manifest is en only', () => {
    languages(['sv-SE', 'sv'])
    document.documentElement.setAttribute('lang', 'sv')
    run()
    expect(document.documentElement).toHaveAttribute('lang', 'en')
  })

  it('ignores a saved sv', () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'sv')
    run()
    expect(document.documentElement).toHaveAttribute('lang', 'en')
  })

  it('uses a manifest language', () => {
    languages(['en-GB'])
    run()
    expect(document.documentElement).toHaveAttribute('lang', 'en')
  })

  it('does not throw when storage does', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(run).not.toThrow()
    expect(document.documentElement).toHaveAttribute('lang', 'en')
  })
})

describe('t, fallback and plurals', () => {
  it('fills named placeholders and leaves unknown ones as written', () => {
    expect(translate('en', 'usage.underOneCent', { amount: '$0.01' })).toBe('under $0.01')
    expect(translate('en', 'usage.underOneCent')).toBe('under {amount}')
    expect(translate('en', 'usage.underOneCent', { other: 1 })).toBe('under {amount}')
  })

  it('shows English for a locale with no catalogue loaded', () => {
    expect(translate('xx', 'format.soonUnknown')).toBe('a few minutes from now')
  })

  it('loads a catalogue on demand and falls back per key', async () => {
    expect(await loadCatalogue('xx', loaders)).toBe(true)
    expect(translate('xx', 'usage.underOneCent', { amount: '$1', count: 1 })).toBe('ett $1')
    expect(translate('xx', 'usage.underOneCent', { amount: '$1', count: 2 })).toBe('under $1 (xx)')
    expect(translate('xx', 'format.soonUnknown')).toBe('a few minutes from now')
  })

  it('loads nothing for a locale the map does not name, or a hostile one', async () => {
    expect(await loadCatalogue('sv', loaders)).toBe(false)
    expect(await loadCatalogue('__proto__', loaders)).toBe(false)
    expect(await loadCatalogue('../x', loaders)).toBe(false)
  })

  it('treats a failed load as English', async () => {
    const broken = { xx: () => Promise.reject(new Error('chunk')) }
    expect(await loadCatalogue('xx', broken)).toBe(false)
    expect(translate('xx', 'usage.underOneCent', { amount: 'a' })).toBe('under a')
  })

  it('chooses plural forms with Intl.PluralRules', () => {
    const forms = { one: '{count} class', other: '{count} classes' }
    expect(plural('en', 1, forms)).toBe('{count} class')
    expect(plural('en', 2, forms)).toBe('{count} classes')
    expect(plural('en', 0, { other: 'x' })).toBe('x')
  })

  it('the active locale feeds t', () => {
    expect(getActiveLocale()).toBe('en')
    expect(t('format.soonUnknown')).toBe('a few minutes from now')
  })
})

describe('React helpers', () => {
  function Probe() {
    const tt = useT()
    return <p>{tt('format.soonUnknown')}</p>
  }

  it('useT works without a provider, in English', () => {
    render(<Probe />)
    expect(screen.getByText('a few minutes from now')).toBeInTheDocument()
  })

  it('the provider keeps English and sets lang for the manifest locale', async () => {
    await act(async () => {
      render(
        <I18nProvider initial="en">
          <Probe />
        </I18nProvider>,
      )
      await Promise.resolve()
    })
    expect(screen.getByText('a few minutes from now')).toBeInTheDocument()
    expect(document.documentElement).toHaveAttribute('lang', 'en')
  })

  it('Trans puts named slots into the sentence and drops nothing else', () => {
    render(
      <p data-testid="p">
        <Trans id="usage.underOneCent" slots={{ amount: <b>$0.01</b> }} />
      </p>,
    )
    expect(screen.getByTestId('p')).toHaveTextContent('under $0.01')
    expect(screen.getByText('$0.01').tagName).toBe('B')
  })
})
