import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

const base = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}

const opener = () => screen.findByRole('button', { name: 'Change' })

describe('ChangeEmail on Account', () => {
  it('opens the form on demand, with focus in the labelled field', async () => {
    fakeApi(base)
    renderAt('/account#security')
    const button = await opener()
    expect(button).toHaveAttribute('aria-expanded', 'false')
    const controls = button.getAttribute('aria-controls')
    expect(controls).toBeTruthy()
    fireEvent.click(button)
    const field = screen.getByLabelText('New email address')
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById(controls as string)).toContainElement(field)
    expect(field).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Change email' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('closes on Cancel, drops the typed address and returns focus to the button', async () => {
    const api = fakeApi(base)
    renderAt('/account#security')
    const button = await opener()
    fireEvent.click(button)
    fireEvent.change(screen.getByLabelText('New email address'), { target: { value: 'half@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByLabelText('New email address')).toBeNull()
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(button).toHaveFocus()
    expect(api.to('POST', '/api/auth/email/change')).toHaveLength(0)
    fireEvent.click(button)
    expect(screen.getByLabelText('New email address')).toHaveValue('')
  })

  it('closes on Escape', async () => {
    fakeApi(base)
    renderAt('/account#security')
    const button = await opener()
    fireEvent.click(button)
    fireEvent.keyDown(screen.getByLabelText('New email address'), { key: 'Escape' })
    expect(screen.queryByLabelText('New email address')).toBeNull()
    expect(button).toHaveFocus()
  })

  it('sends the request, then closes and says to check the inbox', async () => {
    const api = fakeApi({ ...base, 'POST /api/auth/email/change': new Response(null, { status: 202 }) })
    renderAt('/account#security')
    const button = await opener()
    fireEvent.click(button)
    fireEvent.change(screen.getByLabelText('New email address'), { target: { value: ' new@example.test ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change email' }))
    expect(await screen.findByText(/If new@example.test can be used/)).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/email/change')[0]?.body).toEqual({ new_email: 'new@example.test' })
    await waitFor(() => {
      expect(screen.queryByLabelText('New email address')).toBeNull()
    })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(button).toHaveFocus()
  })

  it('keeps the form open and shows the error when the request fails', async () => {
    fakeApi({ ...base, 'POST /api/auth/email/change': new Response(JSON.stringify({ detail: 'nope' }), { status: 409 }) })
    renderAt('/account#security')
    fireEvent.click(await opener())
    fireEvent.change(screen.getByLabelText('New email address'), { target: { value: 'new@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change email' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByLabelText('New email address')).toHaveValue('new@example.test')
    expect(screen.queryByText(/we sent a confirmation link/)).toBeNull()
  })
})
