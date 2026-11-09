import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { AuthSplit } from '../auth/AuthSplit'
import { HowItWorks } from '../landing/HowItWorks'
import { LandingHero } from '../landing/LandingHero'
import { I18nProvider } from '../../i18n/I18n'
import { RouterProvider } from '../../router/Router'
import { SLOTS } from './imageSlots'
import { SlotPicture } from './SlotPicture'

function setTheme(theme: 'dark' | 'light') {
  document.documentElement.setAttribute('data-theme', theme)
}

const wrap = (ui: React.ReactNode) =>
  render(
    <I18nProvider initial="en">
      <RouterProvider>{ui}</RouterProvider>
    </I18nProvider>,
  )

afterEach(() => {
  document.documentElement.removeAttribute('data-theme')
})

describe('SlotPicture', () => {
  it('draws AVIF, WebP and JPEG, the phone crop first, with the size set and the hero loaded first', () => {
    setTheme('dark')
    const { container } = wrap(<SlotPicture slot="hero" priority sizes="100vw" />)
    const sources = [...container.querySelectorAll('source')]
    expect(sources.map((s) => [s.getAttribute('media'), s.getAttribute('type')])).toEqual([
      ['(max-width: 40rem)', 'image/avif'],
      ['(max-width: 40rem)', 'image/webp'],
      ['(max-width: 40rem)', 'image/jpeg'],
      [null, 'image/avif'],
      [null, 'image/webp'],
      [null, 'image/jpeg'],
    ])
    expect(sources[0]?.getAttribute('srcset')).toBe(
      '/img/hero-dark-phone-480.avif 480w, /img/hero-dark-phone-752.avif 752w',
    )
    const img = container.querySelector('img')
    expect(img).toHaveAttribute('src', '/img/hero-dark-1600.jpg')
    expect(img).toHaveAttribute('width', '1600')
    expect(img).toHaveAttribute('height', '900')
    expect(img).toHaveAttribute('loading', 'eager')
    expect(img).toHaveAttribute('fetchpriority', 'high')
    // decorative: empty alt and hidden from assistive technology
    expect(img).toHaveAttribute('alt', '')
    expect(img).toHaveAttribute('aria-hidden', 'true')
  })

  it('follows the theme: the light hero on light, so only one hero is in the page', () => {
    setTheme('light')
    const { container } = wrap(<SlotPicture slot="hero" sizes="100vw" />)
    expect(container.querySelector('img')).toHaveAttribute('src', '/img/hero-light-1600.jpg')
    expect(container.querySelectorAll('img')).toHaveLength(1)
    expect(container.querySelector('img')).toHaveAttribute('loading', 'lazy')
  })

  it('gives a described picture its alt text, and dims a light picture on dark', () => {
    setTheme('dark')
    wrap(<SlotPicture slot="stepDescribe" sizes="100vw" />)
    expect(screen.getByRole('img', { name: 'An instructor writing a class plan on a notepad' })).toBeInTheDocument()
    expect(document.querySelector('[data-slot="stepDescribe"]')).toHaveClass('slot--dim')
  })

  it('draws nothing for a slot with no picture', () => {
    setTheme('light')
    const { container } = wrap(<SlotPicture slot="stepSave" sizes="100vw" />)
    expect(container.querySelector('picture')).toBeNull()
  })
})

describe('HowItWorks', () => {
  it('shows no pictures while a step has none, in either theme (the row never mixes)', () => {
    expect(SLOTS.stepSave.sets).toEqual({})
    for (const theme of ['light', 'dark'] as const) {
      setTheme(theme)
      const { container, unmount } = wrap(<HowItWorks />)
      expect(container.querySelectorAll('picture')).toHaveLength(0)
      expect(within(container).getAllByRole('listitem')).toHaveLength(3)
      unmount()
    }
  })

  it('shows a picture on every card once every step has one', () => {
    const saved = SLOTS.stepSave
    SLOTS.stepSave = { ...saved, sets: SLOTS.stepApprove.sets }
    try {
      setTheme('light')
      const { container } = wrap(<HowItWorks />)
      expect(container.querySelectorAll('picture')).toHaveLength(3)
    } finally {
      SLOTS.stepSave = saved
    }
  })
})

describe('LandingHero', () => {
  it('keeps the title, the lead and the one button on top of a decorative picture', () => {
    setTheme('dark')
    const { container } = wrap(<LandingHero />)
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Join the beta' })).toHaveAttribute('href', '/en/signup')
    expect(container.querySelectorAll('img')).toHaveLength(1)
    expect(container.querySelector('img')).toHaveAttribute('alt', '')
  })
})

describe('AuthSplit', () => {
  it('puts a decorative panel beside the card, shown by CSS on a desktop only', () => {
    setTheme('light')
    const { container } = wrap(
      <AuthSplit>
        <section>card</section>
      </AuthSplit>,
    )
    expect(screen.getByText('card')).toBeInTheDocument()
    const panel = container.querySelector('aside.auth-panel')
    expect(panel).toHaveAttribute('aria-hidden', 'true')
    expect(panel?.querySelector('img')).toHaveAttribute('alt', '')
    expect(container.firstElementChild).toHaveClass('auth-split--panel')
  })

  it('leaves the card alone when the theme has no panel picture', () => {
    setTheme('dark')
    const saved = SLOTS.authPanel
    SLOTS.authPanel = { ...saved, onDark: 'hide' }
    try {
      const { container } = wrap(
        <AuthSplit>
          <section>card</section>
        </AuthSplit>,
      )
      expect(container.querySelector('aside')).toBeNull()
      expect(container.firstElementChild).not.toHaveClass('auth-split--panel')
    } finally {
      SLOTS.authPanel = saved
    }
  })
})
