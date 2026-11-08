import type { Revision } from '../../api/classes'
import { isRunningDraft } from '../../api/jobs'
import { kindLabel, originLabel } from '../../api/classMessages'
import type { Song } from '../../api/songs'
import { useT } from '../../i18n/I18n'
import { tssApproximateNote } from '../../lib/classCards'
import { cadenceText, flatten, inclineText, zoneName } from '../../lib/profile'
import { clock, segmentSeconds, totalSeconds, tss } from '../../lib/workout'
import { Card } from '../ui/card'
import { PatternNotes } from './PatternNotes'
import { ProfileChart } from './ProfileChart'
import { TssValue } from './TssValue'
import { ZoneTime } from './ZoneTime'

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-md bg-raised px-4 py-3">
      <dt className="text-[0.8125rem] text-muted">{label}</dt>
      <dd className="m-0 font-display text-xl font-bold tabular-nums">{children}</dd>
    </div>
  )
}

/**
 * One version of the class: stat tiles, warnings, the profile on the stage, the time in each zone
 * and "Song by song" (the text alternative of the chart). Names, descriptions, cues and song titles
 * are model or user text and are rendered as text only. `musicChanged` is the backend's flag from
 * the answer that made this version, when it has one. `songs` is the class's approved song list,
 * or null while there is none (the strip under the axis is then hidden).
 */
export function RevisionView({
  revision,
  musicChanged,
  songs = null,
  classId = null,
}: {
  revision: Revision
  musicChanged: boolean
  songs?: Song[] | null
  /** The class, for dismissing pattern notes; without it the notes block is not shown. */
  classId?: string | null
}) {
  const t = useT()
  const seconds = totalSeconds(revision.segments)
  const bars = flatten(revision.segments)
  const running = isRunningDraft(revision.segments)
  const hardest = bars.length === 0 ? null : Math.max(...bars.map((b) => b.zone))
  return (
    <article className="flex flex-col gap-5" aria-labelledby="revision-title">
      <div className="flex flex-col gap-1">
        <h2 id="revision-title" className="m-0 font-display text-2xl font-bold">
          {revision.name}
        </h2>
        <p className="m-0 text-muted">
          {t('class.revision.heading', {
            number: revision.number,
            origin: originLabel(revision.origin).toLowerCase(),
          })}
        </p>
        {revision.description ? <p className="m-0">{revision.description}</p> : null}
      </div>
      <dl className="m-0 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Tile label={t('class.revision.length')}>
          <span data-testid="duration">{clock(seconds)}</span>
        </Tile>
        {running ? null : <Tile label={t('class.revision.tss')}>
          <span data-testid="tss">
            <TssValue value={tss(revision.segments)} approximate={revision.tssApproximate} />
          </span>
          {revision.tssApproximate ? (
            <small className="block text-[0.75rem] font-normal text-muted" data-testid="tss-note">
              ({tssApproximateNote()})
            </small>
          ) : null}
        </Tile>}
        <Tile label={t('class.revision.segments')}>{revision.segments.length}</Tile>
        {songs !== null && songs.length > 0 ? <Tile label={t('class.revision.songs')}>{songs.length}</Tile> : null}
        {hardest !== null ? (
          <Tile label={t('class.revision.hardest')}>
            {t('class.zone.short', { zone: hardest })} <span className="text-[0.9375rem] font-normal text-muted">{zoneName(hardest)}</span>
          </Tile>
        ) : null}
      </dl>
      {classId !== null ? (
        <PatternNotes
          classId={classId}
          hits={revision.ruleHits}
          notesUseCurrentRules={revision.notesUseCurrentRules}
        />
      ) : null}
      {musicChanged ? (
        <div className="notice" data-kind="info" role="status" data-testid="music-changed">
          <p>
            {t('class.revision.musicChanged')}
          </p>
        </div>
      ) : null}
      {revision.unknownBpm.length > 0 ? (
        <div className="notice" data-kind="info" role="status" data-testid="unknown-bpm">
          <p>{t('class.unknownBpm')}</p>
          <ul>
            {revision.unknownBpm.map((track) => (
              <li key={track.index}>{track.title}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <ProfileChart segments={revision.segments} title={revision.name} songs={songs} />
      <ZoneTime bars={bars} running={running} />
      <section aria-labelledby="song-by-song-title" className="flex flex-col gap-4">
        <h3 id="song-by-song-title" className="m-0 font-display text-lg font-bold">
          {t('class.revision.songBySong')}
        </h3>
        {revision.segments.map((segment, index) => (
          <Card key={index} role="region" aria-label={segment.name} className="flex flex-col gap-2 p-4">
            <h4 className="m-0 font-display text-base font-bold">
              {segment.name}{' '}
              <span className="text-[0.9375rem] font-normal text-muted">
                {kindLabel(segment.kind)},{' '}
                {clock(segmentSeconds(segment))}
              </span>
            </h4>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-[0.9375rem]">
                <thead>
                  <tr className="text-[0.8125rem] text-muted">
                    <th scope="col" className="py-1 pr-3 font-medium">{t('class.column.length')}</th>
                    <th scope="col" className="py-1 pr-3 font-medium">{t('class.column.zone')}</th>
                    {running ? (
                      <th scope="col" className="py-1 pr-3 font-medium">{t('class.column.incline')}</th>
                    ) : (
                      <>
                        <th scope="col" className="py-1 pr-3 font-medium">{t('class.column.cadence')}</th>
                        <th scope="col" className="py-1 pr-3 font-medium">{t('class.column.position')}</th>
                      </>
                    )}
                    <th scope="col" className="py-1 font-medium">{t('class.column.cue')}</th>
                  </tr>
                </thead>
                <tbody>
                  {segment.intervals.map((interval, row) => (
                    <tr key={row} className="border-t border-line">
                      <td className="py-1 pr-3 tabular-nums">{clock(interval.sec)}</td>
                      <td className="py-1 pr-3">
                        <span className="inline-flex items-center gap-2">
                          <span
                            aria-hidden="true"
                            className="inline-block h-4 w-1 rounded-full"
                            style={{ background: `var(--cb-zone-${String(Math.min(Math.max(Math.round(interval.zone), 1), 7))})` }}
                          />
                          {interval.zone}
                        </span>
                      </td>
                      {running ? (
                        <td className="py-1 pr-3">{inclineText(interval.gradient)}</td>
                      ) : (
                        <>
                          <td className="py-1 pr-3">{cadenceText(interval.rpm)}</td>
                          <td className="py-1 pr-3">{interval.standing ? t('class.position.standing') : t('class.position.seated')}</td>
                        </>
                      )}
                      <td className="py-1">{interval.cue}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ))}
      </section>
    </article>
  )
}
