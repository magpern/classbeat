import type { DraftSegment } from '../../api/draftTypes'
import { useT } from '../../i18n/I18n'
import { cadenceText, flatten, inclineText, zoneName } from '../../lib/profile'
import { clock, totalSeconds } from '../../lib/workout'

const CELL = 'border-b border-line py-2 pr-4 align-top'

/**
 * Every interval of an example class, in order: the text equivalent of the chart (same segments,
 * same order, same lengths and zones), with the total at the foot. The region scrolls on a narrow
 * screen, so it takes focus and has a name.
 */
export function ExampleTable({ segments, title, running }: { segments: DraftSegment[]; title: string; running: boolean }) {
  const t = useT()
  const bars = flatten(segments)
  const caption = t('examples.table.caption', { title })
  return (
    <div className="relative overflow-x-auto" tabIndex={0} role="region" aria-label={caption}>
      <table className="w-full min-w-[34rem] border-collapse text-left text-[0.9375rem]" data-testid="example-table">
        <caption className="sr-only">{caption}</caption>
        <thead className="text-muted">
          <tr>
            {(['part', 'start', 'length', 'zone', 'setting', 'cue'] as const).map((column) => (
              <th key={column} scope="col" className={`${CELL} font-medium`}>
                {t(`examples.col.${column}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bars.map((bar, i) => (
            <tr key={i} data-row={i} data-zone={bar.zone} data-sec={bar.sec}>
              <th scope="row" className={`${CELL} font-medium`}>
                {bar.part}
              </th>
              <td className={`${CELL} tabular-nums`}>{clock(bar.start)}</td>
              <td className={`${CELL} tabular-nums`}>{clock(bar.sec)}</td>
              <td className={CELL}>{t('class.chart.zoneName', { zone: bar.zone, name: zoneName(bar.zone) })}</td>
              <td className={CELL}>
                {running ? (
                  inclineText(bar.gradient)
                ) : (
                  <>
                    {cadenceText(bar.rpm)}
                    {', '}
                    {bar.standing ? t('class.position.standingLower') : t('class.position.seatedLower')}
                  </>
                )}
              </td>
              <td className={CELL}>{bar.cue}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row" colSpan={2} className="py-2 pr-4 font-medium">
              {t('examples.col.total')}
            </th>
            <td className="py-2 pr-4 tabular-nums" data-testid="example-total">
              {clock(totalSeconds(segments))}
            </td>
            <td colSpan={3} />
          </tr>
        </tfoot>
      </table>
    </div>
  )
}
