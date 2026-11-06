import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  confirmTotp,
  getMfaStatus,
  regenerateRecoveryCodes,
  removeTotp,
  startTotp,
  verifyMfa,
  type Enrollment,
  type MfaStatus,
} from '../../api/auth'
import { useGuard } from '../../auth/prompts'
import { useSession } from '../../auth/SessionContext'
import { Trans, useT } from '../../i18n/I18n'
import { Field, Notice, Section, useAction } from '../forms'
import { QrCode } from './QrCode'

/** Shown once after enrolment or regeneration. */
function RecoveryCodes({ codes }: { codes: string[] }) {
  const t = useT()
  return (
    <div className="codes">
      <p>
        <Trans
          id="mfa.recoveryText"
          slots={{ title: <strong>{t('mfa.recoveryTitle')}</strong> }}
        />
      </p>
      <ul aria-label={t('mfa.recoveryList')}>
        {codes.map((code) => (
          <li key={code}>
            <code>{code}</code>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** QR code and manual key, then the first code to confirm. The QR is an image, not markup. */
export function TotpEnroll({ onEnrolled }: { onEnrolled?: () => void }) {
  const t = useT()
  const guard = useGuard()
  const { refresh } = useSession()
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null)
  const [code, setCode] = useState('')
  const [codes, setCodes] = useState<string[] | null>(null)
  const action = useAction()

  const begin = () => {
    void action.run(async () => {
      setEnrollment(await guard(startTotp))
    })
  }
  const confirm = (event: FormEvent) => {
    event.preventDefault()
    if (enrollment === null) return
    void action.run(async () => {
      const saved = await guard(() => confirmTotp(enrollment.pending, code.trim()))
      setCodes(saved)
      setEnrollment(null)
      await refresh()
    })
  }

  if (codes !== null) {
    return (
      <>
        <RecoveryCodes codes={codes} />
        <button
          className="button"
          type="button"
          onClick={() => {
            onEnrolled?.()
          }}
        >
          {t('mfa.saved')}
        </button>
      </>
    )
  }
  if (enrollment === null) {
    return (
      <>
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        <button className="button" type="button" disabled={action.busy} onClick={begin}>
          {t('mfa.setUp')}
        </button>
      </>
    )
  }
  return (
    <>
      <p>{t('mfa.scan')}</p>
      <QrCode svg={enrollment.qrSvg} label={t('mfa.qrLabel')} />
      <p>
        <Trans id="mfa.key" slots={{ key: <code className="break-all">{enrollment.secret}</code> }} />
      </p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={confirm}>
        <Field
          label={t('mfa.codeLabel')}
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(e) => {
            setCode(e.target.value)
          }}
          required
        />
        <button className="button" type="submit" disabled={action.busy}>
          {t('mfa.turnOn')}
        </button>
      </form>
    </>
  )
}

/** The admin second step after sign-in: a TOTP or recovery code. */
export function MfaPrompt({ onVerified }: { onVerified: () => void }) {
  const t = useT()
  const { refresh } = useSession()
  const [code, setCode] = useState('')
  const action = useAction()
  const submit = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      await verifyMfa(code.trim())
      await refresh()
      onVerified()
    })
  }
  return (
    <form onSubmit={submit}>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <Field
        label={t('mfa.verifyLabel')}
        autoComplete="one-time-code"
        value={code}
        onChange={(e) => {
          setCode(e.target.value)
        }}
        required
      />
      <button className="button" type="submit" disabled={action.busy}>
        {t('mfa.verify')}
      </button>
    </form>
  )
}

export function TwoFactor() {
  const t = useT()
  const guard = useGuard()
  const { refresh } = useSession()
  const [status, setStatus] = useState<MfaStatus | null>(null)
  const [codes, setCodes] = useState<string[] | null>(null)
  const [confirmingRemove, setConfirmingRemove] = useState(false)
  const action = useAction()

  const load = useCallback(async () => {
    setStatus(await getMfaStatus())
  }, [])
  useEffect(() => {
    let current = true
    getMfaStatus().then(
      (loaded) => {
        if (current) setStatus(loaded)
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [])

  const regenerate = () => {
    void action.run(async () => {
      setCodes(await guard(regenerateRecoveryCodes))
      await load()
    })
  }
  const remove = () => {
    setConfirmingRemove(false)
    void action.run(async () => {
      await guard(removeTotp)
      setCodes(null)
      await load()
      await refresh()
    })
  }

  if (status === null) {
    return (
      <Section title={t('mfa.title')}>
        <p>{t('common.loading')}</p>
      </Section>
    )
  }
  return (
    <Section title={t('mfa.title')}>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {status.totpEnabled ? (
        <>
          <p>{t('mfa.enabled', { count: status.recoveryCodesLeft })}</p>
          {codes ? <RecoveryCodes codes={codes} /> : null}
          <button
            className="button button--secondary"
            type="button"
            disabled={action.busy}
            onClick={regenerate}
          >
            {t('mfa.newCodes')}
          </button>
          {confirmingRemove ? (
            <div className="notice" role="group" aria-label={t('mfa.confirmRemove')}>
              <p>{t('mfa.removeWarning')}</p>
              <button className="button" type="button" disabled={action.busy} onClick={remove}>
                {t('mfa.yesRemove')}
              </button>
              <button
                className="button button--secondary"
                type="button"
                onClick={() => {
                  setConfirmingRemove(false)
                }}
              >
                {t('mfa.keep')}
              </button>
            </div>
          ) : (
            <button
              className="button button--secondary"
              type="button"
              disabled={action.busy}
              onClick={() => {
                setConfirmingRemove(true)
              }}
            >
              {t('mfa.remove')}
            </button>
          )}
        </>
      ) : (
        <>
          <p>{t('mfa.offText')}</p>
          <TotpEnroll onEnrolled={() => void load()} />
        </>
      )}
    </Section>
  )
}
