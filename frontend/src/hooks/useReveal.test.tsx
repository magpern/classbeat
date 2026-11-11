import { act, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Reveal } from '../components/landing/Reveal'
import { REVEAL_MS } from '../lib/motionPolicy'

// jsdom has no IntersectionObserver: a controllable one stands in for it.
type Callback = (entries: { isIntersecting: boolean }[]) => void
let callbacks: Callback[] = []
class FakeObserver {
  constructor(cb: Callback) {
    callbacks.push(cb)
  }
  observe = vi.fn()
  disconnect = vi.fn()
}

function placeAt(top: number) {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top } as DOMRect)
}

beforeEach(() => {
  callbacks = []
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Reveal', () => {
  it('leaves a block that is already on screen alone', () => {
    placeAt(100)
    render(<Reveal>here</Reveal>)
    expect(screen.getByText('here')).not.toHaveAttribute('data-reveal')
  })

  it('hides a block below the first screen, shows it on scroll, then drops the attribute', () => {
    placeAt(5000)
    render(<Reveal delay={240}>below</Reveal>)
    const block = screen.getByText('below')
    expect(block).toHaveAttribute('data-reveal', 'hidden')
    expect(block.style.getPropertyValue('--reveal-delay')).toBe('240ms')

    act(() => {
      callbacks[0]?.([{ isIntersecting: true }])
    })
    expect(block).toHaveAttribute('data-reveal', 'in')
    act(() => {
      vi.advanceTimersByTime(REVEAL_MS + 240 + 200)
    })
    expect(block).not.toHaveAttribute('data-reveal')
  })

  it('does nothing when the visitor asked for less motion', () => {
    placeAt(5000)
    vi.stubGlobal('matchMedia', () => ({ matches: true }))
    render(<Reveal>calm</Reveal>)
    expect(screen.getByText('calm')).not.toHaveAttribute('data-reveal')
  })

  it('keeps its element: a list item stays a list item', () => {
    placeAt(100)
    render(
      <ol>
        <Reveal as="li">step</Reveal>
      </ol>,
    )
    expect(screen.getByRole('listitem')).toHaveTextContent('step')
  })
})

describe('motion.css', () => {
  it('uses the same reveal time as motionPolicy', () => {
    const css = readFileSync(join(import.meta.dirname, '../styles/motion.css'), 'utf8')
    expect(css).toContain(`opacity ${String(REVEAL_MS / 1000)}s`)
  })
})
