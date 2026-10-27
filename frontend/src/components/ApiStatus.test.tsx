import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HEALTH_TIMEOUT_MS, HEALTH_URL } from '../api/health'
import { ApiStatus } from './ApiStatus'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ApiStatus', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows "API: ok" when /api/health answers ok', async () => {
    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(jsonResponse({ status: 'ok', version: '0.0.0' })),
    )
    vi.stubGlobal('fetch', fetchMock)

    render(<ApiStatus />)

    expect(await screen.findByText('API: ok')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('API: ok')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe(HEALTH_URL)
    expect(HEALTH_URL).toBe('/api/health')
  })

  it('shows "API: checking…" until the answer arrives', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))

    render(<ApiStatus />)

    expect(screen.getByRole('status')).toHaveTextContent('API: checking…')
  })

  it('shows "API: unreachable" when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))

    render(<ApiStatus />)

    expect(await screen.findByText('API: unreachable')).toBeInTheDocument()
  })

  it('shows "API: unreachable" for a non-2xx answer', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ status: 'ok' }, 502))))

    render(<ApiStatus />)

    expect(await screen.findByText('API: unreachable')).toBeInTheDocument()
  })

  it('shows "API: unreachable" when the body is not {"status": "ok"}', async () => {
    // A dev server without a backend answers /api/health with index.html.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('<!doctype html>', { status: 200 }))),
    )

    render(<ApiStatus />)

    expect(await screen.findByText('API: unreachable')).toBeInTheDocument()
  })

  it('shows "API: unreachable" when the API does not answer in time', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () => {
              reject(new DOMException('Aborted', 'AbortError'))
            })
          }),
      ),
    )

    render(<ApiStatus />)
    expect(screen.getByRole('status')).toHaveTextContent('API: checking…')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEALTH_TIMEOUT_MS)
    })

    expect(screen.getByRole('status')).toHaveTextContent('API: unreachable')
  })

  it('cancels the request when it unmounts', () => {
    let signal: AbortSignal | null | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) => {
        signal = init?.signal
        return new Promise<Response>(() => undefined)
      }),
    )

    const { unmount } = render(<ApiStatus />)
    expect(signal?.aborted).toBe(false)

    unmount()

    expect(signal?.aborted).toBe(true)
  })
})
