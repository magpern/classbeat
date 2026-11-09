import { useT } from '../../i18n/I18n'
import { useResolvedTheme } from '../../theme/useResolvedTheme'
import { resolveSlot } from '../imagery/imageSlots'
import { SlotPicture } from '../imagery/SlotPicture'
import { STEPS } from './exampleClass'

/**
 * The three steps. A step's picture is its slot's (`imageSlots.ts`); the pictures are shown when
 * every step has one in the current theme, so the row never mixes cards with and without a picture.
 */
export function HowItWorks() {
  const t = useT()
  const theme = useResolvedTheme()
  const withPictures = STEPS.every((step) => resolveSlot(step.image, theme) !== null)
  return (
    <section aria-labelledby="steps-title" className="flex flex-col gap-4">
      <h2 id="steps-title" className="m-0 font-display text-2xl font-bold">
        {t('landing.how.title')}
      </h2>
      <ol className="m-0 grid list-none grid-cols-1 gap-4 p-0 md:grid-cols-3">
        {STEPS.map((step, i) => (
          <li key={step.title} className="overflow-hidden rounded-lg border border-line bg-surface">
            {withPictures && resolveSlot(step.image, theme) ? (
              <SlotPicture slot={step.image} sizes="(min-width: 48rem) 22rem, 100vw" className="slot--3x2" />
            ) : null}
            <div className="p-5">
              <span
                aria-hidden="true"
                className="mb-3 inline-flex size-9 items-center justify-center rounded-full bg-brand font-display font-bold text-on-brand"
              >
                {String(i + 1)}
              </span>
              <h3 className="m-0 mb-1 font-display text-lg font-bold">{t(step.title)}</h3>
              <p className="m-0 text-[0.9375rem] text-muted">{t(step.text)}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
