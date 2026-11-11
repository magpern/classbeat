import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Button } from '../ui/button'

/** The call to action at the foot of the public pages: one line says sign-up is by invitation. */
export function JoinBetaBand() {
  const t = useT()
  return (
    <section
      aria-labelledby="join-beta"
      className="reveal glow-card flex flex-col items-start gap-3 rounded-lg border border-line bg-surface p-6"
    >
      <h2 id="join-beta" className="m-0 font-display text-2xl font-bold">
        {t('landing.signUp')}
      </h2>
      <p className="m-0 text-muted">{t('pub.cta.line')}</p>
      <Button asChild>
        <Link to="/signup">{t('landing.signUp')}</Link>
      </Button>
    </section>
  )
}
