import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

// The admin Mail page (#653): the response shapes of backend/src/classbeat/admin/mail.py.

const ADMIN = { ...SIGNED_IN, user: { ...SIGNED_IN.user, role: 'admin' } }
const MAIL = '/api/admin/mail'
const SETTING = '/api/admin/settings'

function flagRow(key: string, values: Record<string, unknown>) {
  return {
    key,
    source: 'environment',
    effective: null,
    environment: null,
    stored: null,
    stored_valid: true,
    updated_at: null,
    updated_by: null,
    ...values,
  }
}

function view(disabled: string[] = []) {
  const kind = (id: string, essential: boolean) => ({
    id,
    category: essential ? 'essential' : 'account_activity',
    essential,
    user_can_opt_out: !essential,
    admin_can_disable: !essential,
    enabled: !disabled.includes(id),
  })
  return {
    settings: [
      flagRow('mail_sender_name', { effective: 'classbeat', environment: 'classbeat' }),
      flagRow('mail_support_email', { effective: 'support@classbeat.eu', environment: 'support@classbeat.eu' }),
      flagRow('mail_footer', {}),
      flagRow('mail_kinds_disabled', { effective: disabled, environment: [] }),
    ],
    kinds: [kind('password_reset', true), kind('totp_enrolled', false)],
  }
}

function adminApi(routes: Record<string, unknown>) {
  return fakeApi({
    'GET /api/auth/session': ADMIN,
    'GET /api/admin/whoami': { email: 'ann@example.test' },
    ...(routes as Record<string, object>),
  })
}

async function enterStepUpCode() {
  const dialog = await screen.findByRole('dialog', { name: 'Enter a two-factor code' })
  fireEvent.change(within(dialog).getByLabelText('Authenticator or recovery code'), { target: { value: '424242' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))
}

describe('admin mail', () => {
  it('locks essential kinds on and lets an optional one be switched off', async () => {
    const api = adminApi({
      [`GET ${MAIL}`]: view(),
      [`PUT ${SETTING}/mail_kinds_disabled`]: flagRow('mail_kinds_disabled', { stored: ['totp_enrolled'] }),
    })
    renderAt('/admin/mail')
    const locked = await screen.findByRole('switch', { name: 'password reset' })
    expect(locked).toBeDisabled()
    expect(locked).toBeChecked()
    expect(locked).toHaveAccessibleDescription('Always sent')
    fireEvent.click(screen.getByRole('switch', { name: 'totp enrolled' }))
    await waitFor(() => {
      expect(api.to('PUT', `${SETTING}/mail_kinds_disabled`)).toHaveLength(1)
    })
    expect(api.to('PUT', `${SETTING}/mail_kinds_disabled`)[0]?.body).toEqual({ value: ['totp_enrolled'] })
  })

  it('saves the sender name through the step-up', async () => {
    let stepped = false
    const api = adminApi({
      [`GET ${MAIL}`]: view(),
      [`PUT ${SETTING}/mail_sender_name`]: () => {
        if (!stepped) return reason(403, 'stepup_required')
        return flagRow('mail_sender_name', { source: 'database', effective: 'Studio', stored: 'Studio' })
      },
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
    })
    renderAt('/admin/mail')
    fireEvent.change(await screen.findByRole('textbox', { name: 'Sender name' }), { target: { value: 'Studio' } })
    fireEvent.click(within(screen.getByRole('form', { name: 'Change the sender name' })).getByRole('button', { name: 'Save' }))
    await enterStepUpCode()
    await waitFor(() => {
      expect(api.to('PUT', `${SETTING}/mail_sender_name`)).toHaveLength(2)
    })
    expect(api.to('PUT', `${SETTING}/mail_sender_name`)[1]?.body).toEqual({ value: 'Studio' })
  })

  it('previews in a sandboxed frame, toggles the part and sends a test', async () => {
    const api = adminApi({ [`GET ${MAIL}`]: view(), [`POST ${MAIL}/totp_enrolled/test`]: { sent: true } })
    renderAt('/admin/mail')
    const frame = await screen.findByTitle('Mail preview')
    expect(frame).toHaveAttribute('sandbox', '')
    expect(frame).toHaveAttribute('src', `${MAIL}/password_reset/preview?part=html`)
    fireEvent.click(screen.getByRole('button', { name: 'Text' }))
    expect(screen.getByTitle('Mail preview')).toHaveAttribute('src', `${MAIL}/password_reset/preview?part=text`)
    fireEvent.change(screen.getByLabelText('Mail to preview'), { target: { value: 'totp_enrolled' } })
    expect(screen.getByTitle('Mail preview')).toHaveAttribute('src', `${MAIL}/totp_enrolled/preview?part=text`)
    fireEvent.click(screen.getByRole('button', { name: 'Send test to me' }))
    await waitFor(() => {
      expect(api.to('POST', `${MAIL}/totp_enrolled/test`)).toHaveLength(1)
    })
    expect(await screen.findByText('Sent to your own address.')).toBeInTheDocument()
  })

  it('shows a refused write in words', async () => {
    adminApi({
      [`GET ${MAIL}`]: view(),
      [`PUT ${SETTING}/mail_footer`]: reason(422, 'invalid_value'),
    })
    renderAt('/admin/mail')
    fireEvent.change(await screen.findByLabelText('Footer text'), { target: { value: 'hello' } })
    fireEvent.click(within(screen.getByRole('form', { name: 'Change the footer' })).getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('That value is not allowed here.')).toBeInTheDocument()
  })
})
