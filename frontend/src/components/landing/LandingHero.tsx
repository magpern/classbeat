import { useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { Link } from '../../router/Router'
import { SlotPicture } from '../imagery/SlotPicture'
import { Flywheel } from './Flywheel'

/** The Landing's first screen: the photo behind the title and the one button (ADR 0013 section 5). */
export function LandingHero() {
  const t = useT()
  return (
    <section className="hero" aria-labelledby="landing-title">
      <SlotPicture slot="hero" priority sizes="(max-width: 40rem) 100vw, 1024px" className="slot--fill" />
      <Flywheel />
      <div className="hero__text flex flex-col gap-5">
        <p className="badge w-fit">
          <span className="pulse-dot" aria-hidden="true" />
          {t('landing.badge')}
        </p>
        <h1
          id="landing-title"
          className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight text-balance"
        >
          {t('landing.title')}
        </h1>
        <p className="m-0 max-w-prose text-lg text-muted">{t('landing.lead')}</p>
        <p className="m-0">
          <Button asChild>
            <Link to="/signup">{t('landing.signUp')}</Link>
          </Button>
        </p>
      </div>
    </section>
  )
}
