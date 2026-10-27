import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { App } from './App'

describe('App shell', () => {
  it('renders the product name in the header', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))

    render(<App />)

    expect(within(screen.getByRole('banner')).getByText('classbeat')).toBeInTheDocument()
  })

  it('renders the landing section and the API status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ status: 'ok' }), {
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      ),
    )

    render(<App />)

    const landing = screen.getByRole('region', { name: /classbeat picks the songs/i })
    expect(within(landing).getByText('Private beta')).toBeInTheDocument()
    expect(await within(screen.getByRole('banner')).findByText('API: ok')).toBeInTheDocument()
  })
})
