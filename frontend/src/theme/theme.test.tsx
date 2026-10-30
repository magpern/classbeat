import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import initScript from '../../public/theme-init.js?raw'
import { applyTheme, readTheme, setTheme, THEME_STORAGE_KEY } from './theme'
import { ThemeSwitch } from './ThemeSwitch'

const root = document.documentElement

function breakStorage() {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new DOMException('blocked', 'SecurityError')
  })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('blocked', 'SecurityError')
  })
}

beforeEach(() => {
  window.localStorage.clear()
  root.removeAttribute('data-theme')
})
afterEach(() => {
  root.removeAttribute('data-theme')
})

describe('theme storage', () => {
  it('defaults to Dark when nothing is saved', () => {
    expect(readTheme()).toBe('dark')
  })

  it('reads a saved choice and ignores junk', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light')
    expect(readTheme()).toBe('light')
    window.localStorage.setItem(THEME_STORAGE_KEY, 'purple')
    expect(readTheme()).toBe('dark')
  })

  it('setTheme applies and keeps the choice', () => {
    setTheme('system')
    expect(root).toHaveAttribute('data-theme', 'system')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('system')
  })

  it('still works when storage throws', () => {
    breakStorage()
    expect(readTheme()).toBe('dark')
    expect(() => {
      setTheme('light')
    }).not.toThrow()
    expect(root).toHaveAttribute('data-theme', 'light')
  })
})

describe('theme-init.js (runs before first paint)', () => {
  function runInit() {
    window.eval(initScript)
  }

  it('sets dark with nothing saved', () => {
    runInit()
    expect(root).toHaveAttribute('data-theme', 'dark')
  })

  it.each(['light', 'system', 'dark'])('applies the saved %s', (choice) => {
    window.localStorage.setItem(THEME_STORAGE_KEY, choice)
    runInit()
    expect(root).toHaveAttribute('data-theme', choice)
  })

  it('falls back to dark for an unknown value', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'neon')
    runInit()
    expect(root).toHaveAttribute('data-theme', 'dark')
  })

  it('falls back to dark when storage throws', () => {
    breakStorage()
    expect(() => {
      runInit()
    }).not.toThrow()
    expect(root).toHaveAttribute('data-theme', 'dark')
  })
})

describe('ThemeSwitch', () => {
  it('starts on Dark and offers Light and Same as device', () => {
    render(<ThemeSwitch />)
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Light' })).not.toBeChecked()
    expect(screen.getByRole('radio', { name: 'Same as device' })).not.toBeChecked()
  })

  it('switches to Light, applies it and keeps it', async () => {
    render(<ThemeSwitch />)
    await userEvent.click(screen.getByRole('radio', { name: 'Light' }))
    expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked()
    expect(root).toHaveAttribute('data-theme', 'light')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
  })

  it('switches to Same as device', async () => {
    render(<ThemeSwitch />)
    await userEvent.click(screen.getByRole('radio', { name: 'Same as device' }))
    expect(root).toHaveAttribute('data-theme', 'system')
  })

  it('shows the saved choice', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light')
    render(<ThemeSwitch />)
    expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked()
  })

  it('keeps working when storage throws', async () => {
    breakStorage()
    render(<ThemeSwitch />)
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()
    await userEvent.click(screen.getByRole('radio', { name: 'Light' }))
    expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked()
    expect(root).toHaveAttribute('data-theme', 'light')
  })

  it('keeps the choice when the selected item is pressed again', async () => {
    render(<ThemeSwitch />)
    await userEvent.click(screen.getByRole('radio', { name: 'Dark' }))
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()
  })

  it('applyTheme only sets the attribute', () => {
    applyTheme('light')
    expect(root).toHaveAttribute('data-theme', 'light')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
  })
})
