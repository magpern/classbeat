import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { I18nProvider } from '../i18n/I18n'
import { RouterProvider } from '../router/Router'
import { COOKIE_NOTICE_KEY, CookieNotice } from './CookieNotice'

function mount() {
  return render(
    <I18nProvider>
      <RouterProvider>
        <CookieNotice />
      </RouterProvider>
    </I18nProvider>,
  )
}

describe('CookieNotice', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('names the Cloudflare security check on the sign-in pages and stays informational', () => {
    mount()
    expect(screen.getByTestId('cookie-notice')).toHaveTextContent(/Cloudflare’s security check/)
    expect(screen.queryByRole('button', { name: /accept|reject|decline/i })).toBeNull()
  })

  it('shows the first time, hides after OK and stays hidden after a remount', () => {
    const first = mount()
    expect(screen.getByRole('region', { name: 'Cookie notice' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))
    expect(screen.queryByTestId('cookie-notice')).toBeNull()
    expect(window.localStorage.getItem(COOKIE_NOTICE_KEY)).toBe('1')
    first.unmount()
    mount()
    expect(screen.queryByTestId('cookie-notice')).toBeNull()
  })

  it('publishes its height for the page to leave room, and takes it back when dismissed', () => {
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(96)
    const root = document.documentElement.style
    mount()
    expect(root.getPropertyValue('--cb-notice-measured')).toBe('96px')
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))
    expect(root.getPropertyValue('--cb-notice-measured')).toBe('')
  })

  it('publishes nothing when it was dismissed on an earlier visit', () => {
    window.localStorage.setItem(COOKIE_NOTICE_KEY, '1')
    mount()
    expect(document.documentElement.style.getPropertyValue('--cb-notice-measured')).toBe('')
  })

  it('links to the privacy notice', () => {
    mount()
    expect(screen.getByRole('link', { name: 'privacy notice' })).toHaveAttribute('href', '/en/privacy')
  })

  it('does not crash when storage is unavailable and shows on every load', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const first = mount()
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))
    expect(screen.queryByTestId('cookie-notice')).toBeNull()
    first.unmount()
    mount()
    expect(screen.getByTestId('cookie-notice')).toBeInTheDocument()
  })
})
