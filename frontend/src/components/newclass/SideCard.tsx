import { useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { SketchBars } from './SketchBars'

/** "Your class so far": a sketch on the stage, a summary and the page's one primary button. */
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
    <Card className="flex flex-col gap-4" role="region" aria-labelledby="so-far-title">
      <CardTitle id="so-far-title" className="mb-0">
        {t('newclass.side.title')}
      </CardTitle>
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
      {classesLeft === null ? null : (
        <p className="m-0 text-[0.8125rem] text-muted">
          {classesLeft > 0
            ? t('newclass.side.classesLeft', { count: classesLeft })
            : t('newclass.side.noClassesLeft')}
        </p>
      )}
      <Button type="submit" disabled={busy} className="w-full">
        {t('newclass.side.submit')}
      </Button>
    </Card>
  )
}
