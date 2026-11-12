import { useId, useState, type FormEvent } from 'react'
import { requestInvite } from '../../api/inviteRequest'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Field, Notice, useAction } from '../forms'
import { SegmentedControl } from '../ui/segmented-control'
import { AuthCard } from './AuthCard'
import { AuthSplit } from './AuthSplit'
import { useCaptcha } from './Turnstile'

type Answer = 'yes' | 'no' | ''

/**
 * "Request an invite" (ADR 0033): shown on the sign-up screen only while sign-up is invite-only.
 * Every accepted request gets the same neutral confirmation, so the page can never tell whether an
 * address is known. A refusal (closed, rate limit, the security check) is worded by the catalogue.
 */
export function InviteRequestForm() {
  const t = useT()
  const [email, setEmail] = useState('')
  const [heardFrom, setHeardFrom] = useState('')
  const [instructor, setInstructor] = useState<Answer>('')
  const [missing, setMissing] = useState(false)
  const [sent, setSent] = useState(false)
  const action = useAction()
  const captcha = useCaptcha('invite_request')
  const labelId = useId()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!captcha.ready) return
    if (instructor === '') {
      action.clear()
      setMissing(true)
      return
    }
    const captchaToken = captcha.token
    void action.run(async () => {
      try {
        await requestInvite({
          email,
          heardFrom,
          instructor: instructor === 'yes',
          captchaToken,
        })
        setSent(true)
      } catch (error) {
        // The token was sent, so it is spent whatever the answer: the check runs again.
        if (captchaToken !== null) captcha.reset()
        throw error
      }
    })
  }

  if (sent) {
    return (
      <AuthSplit>
        <AuthCard title={t('inviteRequest.title')}>
          <Notice kind="success">{t('inviteRequest.sent')}</Notice>
          <p className="card__links">
            <Link to="/signup">{t('inviteRequest.back')}</Link>
          </p>
        </AuthCard>
      </AuthSplit>
    )
  }

  return (
    <AuthSplit>
      <AuthCard title={t('inviteRequest.title')}>
        <p className="auth__lead">{t('inviteRequest.lead')}</p>
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        <form onSubmit={submit}>
          <Field
            label={t('common.email')}
            type="email"
            autoComplete="email"
            value={email}
            required
            onChange={(e) => {
              setEmail(e.target.value)
            }}
          />
          <Field
            label={t('inviteRequest.heardFrom')}
            autoComplete="off"
            maxLength={300}
            value={heardFrom}
            onChange={(e) => {
              setHeardFrom(e.target.value)
            }}
          />
          <div className="field">
            <p id={labelId} className="m-0 mb-2">
              {t('inviteRequest.instructor')}
            </p>
            <SegmentedControl<Answer>
              aria-labelledby={labelId}
              value={instructor}
              options={[
                { value: 'yes', label: t('inviteRequest.yes') },
                { value: 'no', label: t('inviteRequest.no') },
              ]}
              onValueChange={(value) => {
                setInstructor(value)
                setMissing(false)
              }}
            />
            {missing ? (
              <p className="field__hint" role="alert">
                {t('inviteRequest.chooseInstructor')}
              </p>
            ) : null}
          </div>
          {captcha.element}
          <button
            className="button button--block"
            type="submit"
            disabled={action.busy || !captcha.ready}
            aria-describedby={captcha.describedBy}
          >
            {t('inviteRequest.submit')}
          </button>
        </form>
        <p className="card__links">
          <Link to="/signup">{t('inviteRequest.back')}</Link>
        </p>
      </AuthCard>
    </AuthSplit>
  )
}
