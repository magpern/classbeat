import { createElement, useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
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
import { Field, Notice, Section, useAction } from '../forms'

// The QR code is drawn inline from the server's SVG (ADR 0003 section 7): the CSP's `img-src`
// allows no `data:` URL (ADR 0013 section 9). Only shapes and their geometry and paint are
// copied, as React elements: no markup is inserted, and nothing in it can load or run.
const QR_ELEMENTS = new Set(['g', 'path', 'rect'])
const QR_ATTRIBUTES = ['d', 'fill', 'stroke', 'transform', 'x', 'y', 'width', 'height'] as const

function qrShapes(parent: Element, prefix: string): ReactNode[] {
  return [...parent.children].flatMap((child, i) => {
    if (!QR_ELEMENTS.has(child.localName)) return []
    const props: Record<string, string> = {}
    for (const name of QR_ATTRIBUTES) {
      const value = child.getAttribute(name)
      if (value !== null) props[name] = value
    }
    const key = `${prefix}.${String(i)}`
    return [createElement(child.localName, { key, ...props }, ...qrShapes(child, key))]
  })
}

function QrCode({ svg, label }: { svg: string; label: string }) {
  const root = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement
  if (root.localName !== 'svg') return null
  const width = root.getAttribute('width') ?? ''
  const height = root.getAttribute('height') ?? ''
  const box = /^\d+(\.\d+)?$/.test(width) && /^\d+(\.\d+)?$/.test(height) ? `0 0 ${width} ${height}` : undefined
  return (
    <svg className="qr" role="img" aria-label={label} viewBox={root.getAttribute('viewBox') ?? box}>
      {qrShapes(root, 'qr')}
    </svg>
  )
}

/** Shown once after enrolment or regeneration. */
function RecoveryCodes({ codes }: { codes: string[] }) {
  return (
    <div className="codes">
      <p>
        <strong>Save these recovery codes now.</strong> Each works once in place of an app code.
        They will not be shown again.
      </p>
      <ul aria-label="Recovery codes">
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
          I have saved them
        </button>
      </>
    )
  }
  if (enrollment === null) {
    return (
      <>
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        <button className="button" type="button" disabled={action.busy} onClick={begin}>
          Set up an authenticator app
        </button>
      </>
    )
  }
  return (
    <>
      <p>Scan this code with an authenticator app, or type the key by hand.</p>
      <QrCode svg={enrollment.qrSvg} label="QR code for your authenticator app" />
      <p>
        Key: <code className="break-all">{enrollment.secret}</code>
      </p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={confirm}>
        <Field
          label="Code from the app"
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(e) => {
            setCode(e.target.value)
          }}
          required
        />
        <button className="button" type="submit" disabled={action.busy}>
          Turn on two-factor
        </button>
      </form>
    </>
  )
}

/** The admin second step after sign-in: a TOTP or recovery code. */
export function MfaPrompt({ onVerified }: { onVerified: () => void }) {
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
        label="Authenticator or recovery code"
        autoComplete="one-time-code"
        value={code}
        onChange={(e) => {
          setCode(e.target.value)
        }}
        required
      />
      <button className="button" type="submit" disabled={action.busy}>
        Verify
      </button>
    </form>
  )
}

export function TwoFactor() {
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
      <Section title="Two-factor authentication">
        <p>Loading</p>
      </Section>
    )
  }
  return (
    <Section title="Two-factor authentication">
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {status.totpEnabled ? (
        <>
          <p>
            An authenticator app is set up. {status.recoveryCodesLeft} recovery codes are left.
          </p>
          {codes ? <RecoveryCodes codes={codes} /> : null}
          <button
            className="button button--secondary"
            type="button"
            disabled={action.busy}
            onClick={regenerate}
          >
            New recovery codes
          </button>
          {confirmingRemove ? (
            <div className="notice" role="group" aria-label="Confirm removing two-factor">
              <p>
                Without two-factor, a stolen password is enough to change this account. Your
                recovery codes stop working too.
              </p>
              <button className="button" type="button" disabled={action.busy} onClick={remove}>
                Yes, remove two-factor
              </button>
              <button
                className="button button--secondary"
                type="button"
                onClick={() => {
                  setConfirmingRemove(false)
                }}
              >
                Keep two-factor
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
              Remove two-factor
            </button>
          )}
        </>
      ) : (
        <>
          <p>
            Adds a code from your phone to the sensitive changes on this page. Admins must set it
            up.
          </p>
          <TotpEnroll onEnrolled={() => void load()} />
        </>
      )}
    </Section>
  )
}
