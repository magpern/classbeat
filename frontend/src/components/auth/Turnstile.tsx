import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import type { CaptchaAction, CaptchaConfig } from '../../api/auth'
import { useSession } from '../../auth/SessionContext'
import type { CatalogueKey } from '../../i18n/en'
import { useLocale, useT } from '../../i18n/I18n'
import { useResolvedTheme } from '../../theme/useResolvedTheme'

// ADR 0025 section 11: Cloudflare Turnstile on the sign-in, sign-up, provider-email and reset-request
// forms. The script is inserted by this file only, on the first mount of a widget (never from
// index.html, never on another route), and stays in the document until the next full load. The
// one exemption of no-third-party-hosts.test.ts is this exact string in this one file.
const SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

/** How long the script may take before the page says it could not load (it may still arrive). */
const LOAD_PATIENCE_MS = 15_000

/** Below this width `flexible` (at least 300 px) does not fit and the widget is `compact`. */
const FLEXIBLE_MIN_PX = 300

interface TurnstileOptions {
  sitekey: string
  action: CaptchaAction
  language: string
  theme: 'dark' | 'light'
  size: 'flexible' | 'compact'
  appearance: 'always'
  execution: 'render'
  'refresh-expired': 'auto'
  'feedback-enabled': false
  'response-field': false
  callback: (token: string) => void
  'expired-callback': () => void
  'timeout-callback': () => void
  'error-callback': () => boolean
  'unsupported-callback': () => void
}

/** The part of Cloudflare's `window.turnstile` this page uses. */
export interface TurnstileApi {
  render: (container: HTMLElement, options: TurnstileOptions) => string | null | undefined
  reset: (widgetId: string) => void
  remove: (widgetId: string) => void
}

declare global {
  interface Window {
    turnstile?: TurnstileApi
  }
}

let loading: Promise<TurnstileApi> | null = null

/** Inserts `api.js` once; a failed load removes its element so the next mount tries again. */
function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile)
  if (loading !== null) return loading
  const attempt = new Promise<TurnstileApi>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_URL
    script.async = true
    const fail = () => {
      script.remove()
      if (loading === attempt) loading = null
      reject(new Error('turnstile did not load'))
    }
    script.addEventListener('load', () => {
      if (window.turnstile) resolve(window.turnstile)
      else fail()
    })
    script.addEventListener('error', fail)
    document.head.append(script)
  })
  loading = attempt
  return attempt
}

/** Test-only: forget a script load so each test starts from a fresh page. */
export function forgetTurnstileForTests(): void {
  loading = null
}

type Problem = 'waiting' | 'expired' | 'error' | 'unsupported' | 'unloaded'

const PROBLEM_TEXT: Record<Problem, CatalogueKey> = {
  waiting: 'captcha.waiting',
  expired: 'captcha.expired',
  error: 'captcha.error',
  unsupported: 'captcha.unsupported',
  unloaded: 'captcha.unloaded',
}

type Report = (token: string | null, problem?: Problem) => void

export interface Captcha {
  /** The token to send as `captcha_token`; null until the check passes, and always while it is off. */
  token: string | null
  /** False while Turnstile is on and no token has arrived: the form must not submit. */
  ready: boolean
  /** After any failed submit: the sent token is spent, so the check runs again. */
  reset: () => void
  /** The reason the submit button waits, for its `aria-describedby`; undefined when ready. */
  describedBy: string | undefined
  /** The check, placed in the form before the submit button; null while Turnstile is off. */
  element: ReactNode
}

/**
 * The security check of one form. Off (`captcha: null` in the session): no element, no script,
 * no token, and the form behaves as before. On: the submit waits for a token, and `reset` after a
 * failed submit asks for a new one. The provider buttons never wait for it (ADR 0025 section 1).
 */
export function useCaptcha(action: CaptchaAction): Captcha {
  const t = useT()
  const { session, status } = useSession()
  const config = session?.captcha ?? null
  const [token, setToken] = useState<string | null>(null)
  const [problem, setProblem] = useState<Problem>('waiting')
  const [round, setRound] = useState(0)
  const statusId = useId()

  const report = useCallback<Report>((next, why) => {
    setToken(next)
    setProblem(why ?? 'waiting')
  }, [])
  const reset = useCallback(() => {
    setToken(null)
    setProblem('waiting')
    setRound((value) => value + 1)
  }, [])

  // Until the session is known (loading, or its fetch failed) it is not known whether the check
  // is on, so the form waits instead of sending a request the server would answer 400 captcha_failed.
  // A failed fetch says so (with the way out, a reload) instead of "checking" for ever.
  const unknown = status !== 'ready'
  const on = config !== null
  const ready = !unknown && (!on || token !== null)
  const element = unknown ? (
    <p id={statusId} className="field__hint" role="status">
      {t(status === 'error' ? 'captcha.session' : 'captcha.waiting')}
    </p>
  ) : on ? (
    <CaptchaBox
      // A new configuration is a new check: nothing of the old one carries over.
      key={config.kind === 'fake' ? 'fake' : config.siteKey}
      config={config}
      action={action}
      round={round}
      statusId={statusId}
      message={token === null ? PROBLEM_TEXT[problem] : null}
      report={report}
    />
  ) : null
  return { token: on && !unknown ? token : null, ready, reset, describedBy: ready ? undefined : statusId, element }
}

function CaptchaBox({
  config,
  action,
  round,
  statusId,
  message,
  report,
}: {
  config: CaptchaConfig
  action: CaptchaAction
  round: number
  statusId: string
  message: CatalogueKey | null
  report: Report
}) {
  const t = useT()
  const labelId = useId()
  useEffect(() => {
    // Mounted with a new configuration: no token yet.
    report(null)
  }, [report])
  return (
    <div className="captcha" role="group" aria-labelledby={labelId}>
      <p id={labelId} className="captcha__label">
        {t('captcha.label')}
      </p>
      {config.kind === 'fake' ? (
        <FakeCheck key={round} report={report} />
      ) : (
        <TurnstileWidget siteKey={config.siteKey} action={action} round={round} report={report} />
      )}
      <p id={statusId} className="field__hint" role="status">
        {message === null ? null : t(message)}
      </p>
    </div>
  )
}

function fakeToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(12))
  return `fake-pass-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`
}

/**
 * The e2e stack's stand-in (`captcha.fake`): a labelled checkbox that yields `fake-pass-<random>`,
 * which the server's fake verifier accepts once. It loads nothing. A test forces a failure by
 * sending another token (the fake verifier refuses `fake-fail`). Remounted on every reset.
 */
function FakeCheck({ report }: { report: Report }) {
  const t = useT()
  const [checked, setChecked] = useState(false)
  return (
    <label className="check">
      <input
        type="checkbox"
        data-testid="captcha-fake"
        checked={checked}
        onChange={(event) => {
          setChecked(event.target.checked)
          report(event.target.checked ? fakeToken() : null)
        }}
      />
      {t('captcha.fake')}
    </label>
  )
}

function TurnstileWidget({
  siteKey,
  action,
  round,
  report,
}: {
  siteKey: string
  action: CaptchaAction
  round: number
  report: Report
}) {
  const { locale } = useLocale()
  const theme = useResolvedTheme()
  const container = useRef<HTMLDivElement>(null)
  const widget = useRef<{ api: TurnstileApi; id: string } | null>(null)
  const [compact, setCompact] = useState(false)

  // `flexible` needs 300 px: measure on mount and when the window is resized, so the widget
  // switches between `flexible` and `compact` (a change renders it again).
  useLayoutEffect(() => {
    const measure = () => {
      const width = container.current?.clientWidth ?? 0
      setCompact(width > 0 && width < FLEXIBLE_MIN_PX)
    }
    measure()
    window.addEventListener('resize', measure)
    return () => {
      window.removeEventListener('resize', measure)
    }
  }, [])

  useEffect(() => {
    let current = true
    let rendered = false
    report(null)
    const patience = window.setTimeout(() => {
      if (current && !rendered) report(null, 'unloaded')
    }, LOAD_PATIENCE_MS)
    loadTurnstile().then(
      (api) => {
        const box = container.current
        if (!current || box === null) return
        rendered = true
        window.clearTimeout(patience)
        const options: TurnstileOptions = {
          sitekey: siteKey,
          action,
          language: locale,
          theme,
          size: compact ? 'compact' : 'flexible',
          appearance: 'always',
          execution: 'render',
          'refresh-expired': 'auto',
          'feedback-enabled': false,
          'response-field': false,
          callback: (token) => {
            if (current) report(token)
          },
          'expired-callback': () => {
            if (current) report(null, 'expired')
          },
          'timeout-callback': () => {
            if (current) report(null, 'expired')
          },
          'error-callback': () => {
            if (current) report(null, 'error')
            return true // handled here: Cloudflare retries and our own text is shown
          },
          'unsupported-callback': () => {
            if (current) report(null, 'unsupported')
          },
        }
        try {
          const id = api.render(box, options)
          if (typeof id === 'string') widget.current = { api, id }
          else report(null, 'error')
        } catch {
          report(null, 'error')
        }
      },
      () => {
        window.clearTimeout(patience)
        if (current) report(null, 'unloaded')
      },
    )
    return () => {
      current = false
      window.clearTimeout(patience)
      const shown = widget.current
      widget.current = null
      if (shown !== null) shown.api.remove(shown.id)
    }
  }, [siteKey, action, locale, theme, compact, report])

  useEffect(() => {
    // A failed submit spent the token: the same widget runs again (the first round is the render).
    const shown = widget.current
    if (round > 0 && shown !== null) shown.api.reset(shown.id)
  }, [round])

  return <div ref={container} className="captcha__widget" />
}
