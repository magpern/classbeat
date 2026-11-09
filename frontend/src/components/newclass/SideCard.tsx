import { useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { CardTitle } from '../ui/card'
import { StickyActionBar } from '../ui/sticky-action-bar'
import { SketchBars } from './SketchBars'

/**
 * "Your class so far" in the shared sticky action bar (ADR 0028 section 2): in the right column on a
 * desktop, with a sketch and a summary above the page's one primary button; on a phone a bottom bar
 * with the classes left and that button (the form above already shows every choice).
 */
export function SideCard({
  profile,
  summary,
  busy,
  classesLeft,
}: {
  profile: [number, number][]
  summary: { label: string; value: string }[]
  busy: boolean
  /** Classes left this month from the plan API; null hides the line. */
  classesLeft: number | null
}) {
  const t = useT()
  return (
    <StickyActionBar label={t('newclass.side.title')} className="lg:p-5">
      <div className="flex flex-col gap-4 max-lg:hidden">
        <CardTitle className="mb-0">{t('newclass.side.title')}</CardTitle>
        <SketchBars profile={profile} />
        <p className="m-0 text-[0.8125rem] text-muted">{t('newclass.side.sketchNote')}</p>
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[0.9375rem]">
          {summary.map((row) => (
            <div key={row.label} className="contents">
              <dt className="text-muted">{row.label}</dt>
              <dd className="m-0 text-right text-text">{row.value}</dd>
            </div>
          ))}
        </dl>
      </div>
      {classesLeft === null ? null : (
        <p className="m-0 min-w-0 text-[0.8125rem] text-muted max-lg:flex-1">
          {classesLeft > 0 ? t('newclass.side.classesLeft', { count: classesLeft }) : t('newclass.side.noClassesLeft')}
        </p>
      )}
      <Button type="submit" disabled={busy} className="w-full max-lg:w-auto max-lg:shrink-0">
        {t('newclass.side.submit')}
      </Button>
    </StickyActionBar>
  )
}
