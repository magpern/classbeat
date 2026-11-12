import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { en } from '../i18n/en'
import { fakeApi, json, reason } from '../test/fakeApi'
import { renderAt } from '../test/render'

// ADR 0033, PB-8 (#986): the "Request an invite" link and form on the sign-up screen.

const PUBLIC_INVITE = {
  banner: null,
  signup_mode: 'invite',
  support_address: 'support@classbeat.eu',
}
const REQUEST = 'POST /api/auth/invite-request'

async function fill(email = 'rider@example.test', answer: 'Yes' | 'No' | null = 'Yes') {
  fireEvent.change(await screen.findByLabelText('Email'), {
    target: { value: email },
  })
  fireEvent.change(screen.getByLabelText(en['inviteRequest.heardFrom']), {
    target: { value: ' a friend ' },
  })
  if (answer !== null) fireEvent.click(screen.getByRole('radio', { name: answer }))
}

describe('request an invite link', () => {
  it('is on the sign-up screen while sign-up is invite-only', async () => {
    fakeApi({ 'GET /api/settings/public': PUBLIC_INVITE })
    renderAt('/signup')
    const link = await screen.findByRole('link', {
      name: en['inviteRequest.link'],
    })
    expect(link).toHaveAttribute('href', '/en/signup?request=invite')
  })

  it.each(['open', 'closed'])('is not shown when sign-up is %s', async (mode) => {
    fakeApi({
      'GET /api/settings/public': { ...PUBLIC_INVITE, signup_mode: mode },
    })
    renderAt('/signup')
    await screen.findByLabelText(en['auth.inviteCode'])
    await waitFor(() => {
      expect(screen.queryByRole('link', { name: en['inviteRequest.link'] })).toBeNull()
    })
  })

  it('is not shown when the mode cannot be read, and the form url falls back to sign-up', async () => {
    fakeApi({ 'GET /api/settings/public': json({}, 500) })
    renderAt('/signup?request=invite')
    await screen.findByLabelText(en['auth.inviteCode'])
    expect(screen.queryByRole('link', { name: en['inviteRequest.link'] })).toBeNull()
    expect(screen.queryByLabelText(en['inviteRequest.heardFrom'])).toBeNull()
  })

  it('is not on the sign-in screen', async () => {
    fakeApi({ 'GET /api/settings/public': PUBLIC_INVITE })
    renderAt('/signin')
    await screen.findByRole('heading', { name: 'Sign in' })
    expect(screen.queryByRole('link', { name: en['inviteRequest.link'] })).toBeNull()
  })
})

describe('request an invite form', () => {
  it('sends the request and shows one neutral confirmation', async () => {
    const api = fakeApi({
      'GET /api/settings/public': PUBLIC_INVITE,
      [REQUEST]: () => json({ status: 'received' }, 202),
    })
    renderAt('/signup?request=invite')
    await fill()
    fireEvent.click(screen.getByRole('button', { name: en['inviteRequest.submit'] }))
    expect(await screen.findByText(en['inviteRequest.sent'])).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/invite-request')[0]?.body).toMatchObject({
      email: 'rider@example.test',
      heard_from: 'a friend',
      instructor: true,
    })
    expect(screen.getByRole('link', { name: en['inviteRequest.back'] })).toHaveAttribute('href', '/en/signup')
  })

  it('sends an empty answer as null and "No" as false', async () => {
    const api = fakeApi({
      'GET /api/settings/public': PUBLIC_INVITE,
      [REQUEST]: () => json({ status: 'received' }, 202),
    })
    renderAt('/signup?request=invite')
    fireEvent.change(await screen.findByLabelText('Email'), {
      target: { value: 'a@example.test' },
    })
    fireEvent.click(screen.getByRole('radio', { name: 'No' }))
    fireEvent.click(screen.getByRole('button', { name: en['inviteRequest.submit'] }))
    await screen.findByText(en['inviteRequest.sent'])
    expect(api.to('POST', '/api/auth/invite-request')[0]?.body).toMatchObject({
      heard_from: null,
      instructor: false,
    })
  })

  it('asks for the yes or no answer and sends nothing without it', async () => {
    const api = fakeApi({ 'GET /api/settings/public': PUBLIC_INVITE })
    renderAt('/signup?request=invite')
    await fill('rider@example.test', null)
    fireEvent.click(screen.getByRole('button', { name: en['inviteRequest.submit'] }))
    expect(await screen.findByText(en['inviteRequest.chooseInstructor'])).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/invite-request')).toHaveLength(0)
  })

  it.each([
    [reason(403, 'requests_closed'), en['error.reason.requests_closed']],
    [reason(400, 'captcha_failed'), en['error.reason.captcha_failed']],
    [reason(503, 'captcha_unavailable'), en['error.reason.captcha_unavailable']],
    [json({}, 429, { 'Retry-After': '120' }), 'Too many attempts. Try again in 2 minutes.'],
    [json({}, 429), en['error.tooMany']],
  ])('words a refusal in plain language (%#)', async (answer, text) => {
    fakeApi({
      'GET /api/settings/public': PUBLIC_INVITE,
      [REQUEST]: () => answer,
    })
    renderAt('/signup?request=invite')
    await fill()
    fireEvent.click(screen.getByRole('button', { name: en['inviteRequest.submit'] }))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(screen.queryByText(en['inviteRequest.sent'])).toBeNull()
  })

  it('waits for the security check and sends its token with the request', async () => {
    const api = fakeApi({
      'GET /api/auth/session': {
        authenticated: false,
        csrf_token: 'a',
        captcha: { fake: true },
      },
      'GET /api/settings/public': PUBLIC_INVITE,
      [REQUEST]: () => json({ status: 'received' }, 202),
    })
    renderAt('/signup?request=invite')
    await fill()
    const submit = screen.getByRole('button', {
      name: en['inviteRequest.submit'],
    })
    expect(submit).toBeDisabled()
    fireEvent.click(await screen.findByTestId('captcha-fake'))
    await waitFor(() => {
      expect(submit).toBeEnabled()
    })
    fireEvent.click(submit)
    await screen.findByText(en['inviteRequest.sent'])
    expect(
      (
        api.to('POST', '/api/auth/invite-request')[0]?.body as {
          captcha_token: string
        }
      ).captcha_token,
    ).toMatch(/^fake-pass-/)
  })

  it('runs the security check again after a refused request, and sends a new token', async () => {
    let refused = false
    const api = fakeApi({
      'GET /api/auth/session': {
        authenticated: false,
        csrf_token: 'a',
        captcha: { fake: true },
      },
      'GET /api/settings/public': PUBLIC_INVITE,
      [REQUEST]: () => {
        if (refused) return json({ status: 'received' }, 202)
        refused = true
        return reason(429, 'rate_limited')
      },
    })
    renderAt('/signup?request=invite')
    await fill()
    const submit = screen.getByRole('button', {
      name: en['inviteRequest.submit'],
    })
    fireEvent.click(await screen.findByTestId('captcha-fake'))
    await waitFor(() => {
      expect(submit).toBeEnabled()
    })
    fireEvent.click(submit)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    // The spent token is dropped: the submit waits for a new check.
    await waitFor(() => {
      expect(submit).toBeDisabled()
    })
    expect(screen.getByTestId('captcha-fake')).not.toBeChecked()
    fireEvent.click(screen.getByTestId('captcha-fake'))
    await waitFor(() => {
      expect(submit).toBeEnabled()
    })
    fireEvent.click(submit)
    await screen.findByText(en['inviteRequest.sent'])
    const tokens = api
      .to('POST', '/api/auth/invite-request')
      .map((call) => (call.body as { captcha_token: string }).captcha_token)
    expect(tokens).toHaveLength(2)
    expect(tokens[0]).toMatch(/^fake-pass-/)
    expect(tokens[1]).toMatch(/^fake-pass-/)
    expect(tokens[1]).not.toBe(tokens[0])
  })
})
