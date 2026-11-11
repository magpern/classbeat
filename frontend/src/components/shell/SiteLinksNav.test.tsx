import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { LegalFooter } from '../../pages/legal/LegalFooter'
import { RouterProvider } from '../../router/Router'
import { visibleSiteLinks } from '../../publicSite/siteLinks'
import { SiteLinksNav } from './SiteLinksNav'

// The header and footer links of the new public pages show only for a published path (ADR 0030, PP4).

function html(node: React.ReactNode): string {
  return renderToString(
    <I18nProvider initial="en">
      <RouterProvider initialLocation={{ pathname: '/en', search: '' }}>{node}</RouterProvider>
    </I18nProvider>,
  )
}

describe('the links to the new pages', () => {
  it('are hidden while their paths are not published', () => {
    expect(visibleSiteLinks()).toEqual([])
    expect(visibleSiteLinks(['', '/privacy', '/terms'])).toEqual([])
    expect(html(<SiteLinksNav />)).toBe('')
    expect(html(<LegalFooter />)).not.toMatch(/how-it-works|features/)
  })

  it('show one link per published path, in the header and the footer', () => {
    const published = ['', '/privacy', '/terms', '/features']
    expect(visibleSiteLinks(published).map((l) => l.path)).toEqual(['/features'])
    const header = html(<SiteLinksNav published={published} />)
    expect(header).toContain('href="/en/features"')
    expect(header).not.toContain('how-it-works')
    const footer = html(<LegalFooter published={[...published, '/how-it-works']} />)
    expect(footer).toContain('href="/en/features"')
    expect(footer).toContain('href="/en/how-it-works"')
    expect(footer).toContain('href="/en/privacy"')
  })
})
