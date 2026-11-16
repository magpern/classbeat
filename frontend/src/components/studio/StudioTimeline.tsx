import type { Timeline } from '../../api/studioOutputs'
import { useT } from '../../i18n/I18n'
import { minutes } from '../../lib/songs'
import { Chip } from '../ui/chip'

/**
 * The music timeline of a studio draft (ADR 0036 section 5): the songs with their cut points, the
 * crossfades and the intervals, all from the server's composed map. No audio: nothing here plays.
 */
export function StudioTimeline({ timeline }: { timeline: Timeline }) {
  const t = useT()
  const fadeAfter = new Map(timeline.crossfades.map((fade) => [fade.after, fade]))
  return (
    <div className="flex flex-col gap-3" data-testid="studio-timeline">
      <h3 className="m-0 font-display text-lg font-bold">{t('studio.tl.title')}</h3>
      <p className="m-0 text-muted">
        {t('studio.tl.length', { mix: minutes(timeline.durationSec), workout: minutes(timeline.workoutSec) })}
      </p>
      <p className="m-0 text-muted">{t('studio.tl.noAudio')}</p>
      <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label={t('studio.tl.songs')}>
        {timeline.tracks.map((track) => {
          const fade = fadeAfter.get(track.position)
          return (
            <li key={track.position} className="flex flex-col gap-1" data-testid="timeline-song">
              <div className="rounded-md border border-line bg-surface p-3">
                <p className="m-0 break-words font-medium text-text">
                  {track.position + 1}. {t('studio.tl.song', { title: track.title, artist: track.artist })}
                </p>
                <p className="m-0 text-[0.9375rem] text-muted">
                  {t('studio.tl.songFacts', {
                    bpm: Math.round(track.bpm),
                    from: minutes(track.startSec),
                    to: minutes(track.endSec),
                    played: minutes(track.playedSec),
                    mixFrom: minutes(track.mixStartSec),
                    mixTo: minutes(track.mixEndSec),
                  })}
                </p>
              </div>
              {fade !== undefined ? (
                <p className="m-0 pl-3 text-[0.9375rem] text-muted" data-testid="timeline-fade">
                  {t('studio.tl.fade', { sec: fade.sec, from: minutes(fade.mixFromSec), to: minutes(fade.mixToSec) })}
                </p>
              ) : null}
            </li>
          )
        })}
      </ol>
      <h3 className="m-0 font-display text-lg font-bold">{t('studio.tl.intervals')}</h3>
      <ol className="m-0 flex list-none flex-col gap-1 p-0" aria-label={t('studio.tl.intervals')}>
        {timeline.intervals.map((interval, n) => (
          <li key={n} className="flex flex-wrap items-center gap-2 text-[0.9375rem] text-text" data-testid="timeline-interval">
            {interval.segmentName !== '' ? <span className="font-medium">{interval.segmentName}</span> : null}
            <span>
              {interval.zone === null
                ? t('studio.tl.intervalNoZone', {
                    start: minutes(interval.startSec),
                    end: minutes(interval.endSec),
                    song: interval.track + 1,
                  })
                : t('studio.tl.interval', {
                    start: minutes(interval.startSec),
                    end: minutes(interval.endSec),
                    zone: interval.zone,
                    song: interval.track + 1,
                  })}
            </span>
            {interval.standing ? <Chip>{t('studio.tl.standing')}</Chip> : null}
          </li>
        ))}
      </ol>
    </div>
  )
}
