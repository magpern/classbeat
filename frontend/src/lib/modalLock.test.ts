import { afterEach, describe, expect, it, vi } from 'vitest'
import { keepTabInside, lockPageBehind } from './modalLock'

afterEach(() => {
  document.body.innerHTML = ''
  document.documentElement.className = ''
})

function page() {
  document.body.innerHTML =
    '<div id="root"><button>Behind</button></div><div id="portal"><div id="dialog"><button id="a">A</button><button id="b">B</button></div></div>'
  const dialog = document.getElementById('dialog')!
  return { dialog, root: document.getElementById('root')!, portal: document.getElementById('portal')! }
}

describe('lockPageBehind', () => {
  it('stops the page scrolling and reaching the rest of the page, and undoes exactly that', () => {
    const { dialog, root, portal } = page()
    const unlock = lockPageBehind(dialog)
    expect(document.documentElement).toHaveClass('overflow-hidden')
    expect(root).toHaveAttribute('inert')
    expect(portal).not.toHaveAttribute('inert')
    expect(document.querySelectorAll('style')).toHaveLength(0)
    expect(document.body.hasAttribute('style')).toBe(false)
    unlock()
    expect(document.documentElement).not.toHaveClass('overflow-hidden')
    expect(root).not.toHaveAttribute('inert')
  })

  it('nests: the page stays locked until the last dialog goes, and a fixed inert stays', () => {
    const { dialog, root } = page()
    root.setAttribute('inert', '') // already inert before: not ours to remove
    const first = lockPageBehind(dialog)
    const second = lockPageBehind(dialog)
    first()
    expect(document.documentElement).toHaveClass('overflow-hidden')
    second()
    expect(document.documentElement).not.toHaveClass('overflow-hidden')
    expect(root).toHaveAttribute('inert')
  })
})

describe('keepTabInside', () => {
  const press = (dialog: HTMLElement, shiftKey: boolean) => {
    const event = { key: 'Tab', shiftKey, preventDefault: vi.fn() }
    keepTabInside(event, dialog)
    return event.preventDefault
  }

  it('wraps from the last control to the first and from the first to the last', () => {
    const { dialog } = page()
    document.getElementById('b')!.focus()
    expect(press(dialog, false)).toHaveBeenCalled()
    expect(document.getElementById('a')).toHaveFocus()
    expect(press(dialog, true)).toHaveBeenCalled()
    expect(document.getElementById('b')).toHaveFocus()
  })

  it('leaves Tab between controls and other keys alone', () => {
    const { dialog } = page()
    document.getElementById('a')!.focus()
    expect(press(dialog, false)).not.toHaveBeenCalled()
    const other = { key: 'Enter', shiftKey: false, preventDefault: vi.fn() }
    document.getElementById('b')!.focus()
    keepTabInside(other, dialog)
    expect(other.preventDefault).not.toHaveBeenCalled()
  })
})
