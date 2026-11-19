import { render } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { usePublishHeight } from './usePublishHeight'

function Bar() {
  const ref = useRef<HTMLElement>(null)
  usePublishHeight(ref, '--cb-test-h')
  return <section ref={ref}>bar</section>
}

const root = () => document.documentElement.style.getPropertyValue('--cb-test-h')

function heightOf(value: number) {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(value)
}

// Resize following is checked in the browser (e2e/tests/cookie-notice-phone.spec.ts): jsdom's ResizeObserver is a fixed stub.
describe('usePublishHeight', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('sets the height on the root before the first paint and removes it on unmount', () => {
    heightOf(87)
    const view = render(<Bar />)
    expect(root()).toBe('87px')
    view.unmount()
    expect(root()).toBe('')
  })

  it('publishes each bar under its own name', () => {
    heightOf(53)
    function Two() {
      const a = useRef<HTMLElement>(null)
      const b = useRef<HTMLElement>(null)
      usePublishHeight(a, '--cb-test-a')
      usePublishHeight(b, '--cb-test-b')
      return (
        <>
          <section ref={a}>a</section>
          <section ref={b}>b</section>
        </>
      )
    }
    const view = render(<Two />)
    expect(document.documentElement.style.getPropertyValue('--cb-test-a')).toBe('53px')
    expect(document.documentElement.style.getPropertyValue('--cb-test-b')).toBe('53px')
    view.unmount()
    expect(document.documentElement.style.getPropertyValue('--cb-test-a')).toBe('')
  })
})
