import type { Revision } from '../../api/classes'
import { isRunningDraft } from '../../api/jobs'
import { kindLabel } from '../../api/classMessages'
import type { Song } from '../../api/songs'
import { useT } from '../../i18n/I18n'
import { GameBadge, GameLicence } from './GameBits'
import { segmentGames, hasGame } from '../../lib/games'
import { cadenceText, flatten, inclineText } from '../../lib/profile'
import { clock, segmentSeconds } from '../../lib/workout'
import { Card } from '../ui/card'
import { PageHeading } from '../ui/page-heading'
import { RevisionStats } from './RevisionStats'
import { versionLine } from './versionLine'
import { PatternNotes } from './PatternNotes'
import { ProfileChart } from './ProfileChart'
import { ZoneTime } from './ZoneTime'

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
  pageTitle = null,
}: {
  revision: Revision
  musicChanged: boolean
  songs?: Song[] | null
  /** The class, for dismissing pattern notes; without it the notes block is not shown. */
  classId?: string | null
  /**
   * The class page's own title. When it is given, the version line is left to the page heading and
   * the version's name is shown only when it differs from the title (an older version's name).
   */
  pageTitle?: string | null
}) {
  const t = useT()
  const bars = flatten(revision.segments)
  const running = isRunningDraft(revision.segments)
  const games = !running && hasGame(revision.segments)
  const showName = pageTitle === null || revision.name !== pageTitle
  return (
    <article
      className="flex flex-col gap-6"
      {...(showName ? { 'aria-labelledby': 'revision-title' } : { 'aria-label': revision.name })}
    >
      {showName || revision.description ? (
        <div className="flex flex-col gap-1">
          {showName ? (
            <h2 id="revision-title" className="m-0 font-display text-2xl font-bold">
              {revision.name}
            </h2>
          ) : null}
          {pageTitle === null ? <p className="m-0 text-muted">{versionLine(t, revision)}</p> : null}
          {revision.description ? <p className="m-0">{revision.description}</p> : null}
        </div>
      ) : null}
      <RevisionStats revision={revision} bars={bars} running={running} songs={songs} />
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
              <li key={track.index} className="break-words">{track.title}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="chart-in relative overflow-hidden rounded-xl border border-line bg-surface p-3 pt-4 shadow-card">
        <div aria-hidden="true" className="absolute inset-x-0 top-0 z-10 h-1 bg-heat" />
        <ProfileChart segments={revision.segments} title={revision.name} songs={songs} />
      </div>
      <ZoneTime bars={bars} running={running} />
      {games ? <GameLicence /> : null}
      <section aria-labelledby="song-by-song-title" className="flex flex-col gap-4">
        <PageHeading size="section" as="h3" titleId="song-by-song-title" title={t('class.revision.songBySong')} />
        {revision.segments.map((segment, index) => (
          <Card key={index} role="region" aria-label={segment.name} className="flex flex-col gap-2 p-4">
            <h4 className="m-0 font-display text-base font-bold">
              {segment.name}{' '}
              <span className="text-[0.9375rem] font-normal text-muted">
                {kindLabel(segment.kind)},{' '}
                {clock(segmentSeconds(segment))}
              </span>
            </h4>
            {games ? segmentGames(segment).map((game) => <GameBadge key={game} game={game} />) : null}
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
