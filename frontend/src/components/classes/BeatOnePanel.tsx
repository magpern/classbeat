import { useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { classReasonText, isStale } from '../../api/classMessages'
import {
  clearAllBeatOne,
  clearBeatOne,
  clearBeatOneRow,
  setBeatOne,
  type BeatOne,
  type BeatOneSong,
  type ClassSummary,
} from '../../api/classes'
import { getConnections } from '../../api/connections'
import { createRefitJob } from '../../api/jobs'
import { useT } from '../../i18n/I18n'
import { t as translate, type TFunction } from '../../i18n/t'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { clock } from '../../lib/workout'
import { Button } from '../ui/button'
import { Notice, useAction } from '../forms'

// Beat-one corrections (ADR 0007 amendment "beat-one corrections", BC5): per song of the current
// version, move beat 1 earlier or later by whole beats. A change is stored at once; the version
// itself only changes when the user applies the fixes (a `refit_draft` job, no model) or asks for
// a change by chat. Titles are data, rendered as text.

const MAX_BEATS = 3

export function beatsInWords(beats: number): string {
  if (beats === 0) return translate('class.beat.asAnalysed')
  return translate(beats > 0 ? 'class.beat.later' : 'class.beat.earlier', { count: Math.abs(beats) })
}

function songName(t: TFunction, song: BeatOneSong): string {
  return song.title !== null && song.title !== '' ? song.title : t('class.beat.song', { song: song.song })
}

export function BeatOnePanel({
  summary,
  busy,
  onReload,
}: {
  summary: ClassSummary
  busy: boolean
  onReload: () => void
}) {
  const t = useT()
  const [view, setView] = useState<BeatOne>(summary.beatOne)
  // A reload of the same version (a refusal that reloads the page, say) brings a new block: the
  // panel keeps its key, so it takes the server's view here, during render (no effect).
  const [loaded, setLoaded] = useState<BeatOne>(summary.beatOne)
  if (loaded !== summary.beatOne) {
    setLoaded(summary.beatOne)
    setView(summary.beatOne)
  }
  const change = useAction()
  const apply = useAction()
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const revisionId = view.revisionId

  const setTo = (song: BeatOneSong, beats: number) => {
    if (revisionId === null) return
    void change
      .run(
        () =>
          beats === 0
            ? clearBeatOne(summary.id, song.song, revisionId)
            : setBeatOne(summary.id, song.song, beats, revisionId),
        (error) => {
          if (isStale(error)) onReload()
          return classReasonText(error)
        },
      )
      .then((next) => {
        if (next !== undefined) setView(next)
      })
  }

  const run = (task: () => Promise<BeatOne>) => {
    void change
      .run(task, (error) => {
        if (isStale(error)) onReload()
        return classReasonText(error)
      })
      .then((next) => {
        if (next !== undefined) setView(next)
      })
  }
  const clearRow = (rowId: string) => {
    run(() => clearBeatOneRow(summary.id, rowId))
  }
  const clearAll = () => {
    run(() => clearAllBeatOne(summary.id))
  }

  const applyFixes = () => {
    if (revisionId === null) return
    const fingerprint = JSON.stringify([summary.id, revisionId, view.songs.map((s) => s.beats)])
    if (attempt.current?.fingerprint !== fingerprint) {
      attempt.current = { fingerprint, key: newIdempotencyKey() }
    }
    const key = attempt.current.key
    void apply
      .run(
        async () => {
          const connection = (await getConnections()).find((c) => c.provider === 'mowl')
          if (connection === undefined || connection.status !== 'connected') {
            throw new ApiError(409, 'not_connected', null, undefined)
          }
          await createRefitJob(
            { class_id: summary.id, base_revision_id: revisionId, mowl_connection_id: connection.id },
            key,
          )
          return true
        },
        (error) => {
          if (isStale(error)) onReload()
          return classReasonText(error)
        },
      )
      .then((sent) => {
        if (sent === true) {
          attempt.current = null
          onReload()
        }
      })
  }

  return (
    <section
      className="panel flex w-full min-w-0 flex-col gap-3"
      aria-labelledby="beat-one-title"
      data-testid="beat-one"
    >
      <h2 id="beat-one-title" className="panel__title m-0">
        {t('class.beat.title')}
      </h2>
      <p className="m-0">
        {t('class.beat.intro')}
      </p>
      <ol className="m-0 flex list-none flex-col gap-3 p-0">
        {view.songs.map((song) => {
          const name = songName(t, song)
          return (
            <li
              key={song.song}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line pb-3"
              data-testid={`beat-one-song-${String(song.song)}`}
            >
              <div className="min-w-0 grow basis-48">
                <p className="m-0 font-medium">{name}</p>
                <p className="m-0 text-muted text-[0.875rem]">
                  {t('class.beat.from', { start: clock(song.start), bpm: Math.round(song.bpm) })}
                </p>
                {song.bpmChanged ? (
                  <p className="m-0 text-muted text-[0.875rem]">
                    {t('class.beat.bpmChanged')}
                  </p>
                ) : null}
                {song.estimate && song.beats === 0 ? (
                  <p className="m-0 text-muted text-[0.875rem]">
                    {t('class.beat.estimate')}
                  </p>
                ) : null}
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-2" role="group" aria-label={t('class.beat.group', { name })}>
                <Button
                  variant="secondary"
                  aria-label={t('class.beat.moveEarlier', { name })}
                  disabled={change.busy || song.beats <= -MAX_BEATS}
                  onClick={() => {
                    setTo(song, song.beats - 1)
                  }}
                >
                  {t('class.beat.earlierButton')}
                </Button>
                <output aria-live="polite" className="min-w-24 text-center">
                  {beatsInWords(song.beats)}
                </output>
                <Button
                  variant="secondary"
                  aria-label={t('class.beat.moveLater', { name })}
                  disabled={change.busy || song.beats >= MAX_BEATS}
                  onClick={() => {
                    setTo(song, song.beats + 1)
                  }}
                >
                  {t('class.beat.laterButton')}
                </Button>
                <Button
                  variant="ghost"
                  aria-label={t('class.beat.reset', { name })}
                  disabled={change.busy || song.beats === 0}
                  onClick={() => {
                    setTo(song, 0)
                  }}
                >
                  {t('class.beat.resetButton')}
                </Button>
              </div>
            </li>
          )
        })}
      </ol>
      {change.error ? <Notice kind="error">{change.error}</Notice> : null}
      {view.stale.length > 0 ? (
        <div className="flex flex-col gap-2" data-testid="beat-one-stale">
          <p className="m-0 text-muted">
            {t('class.beat.stale')}
          </p>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {view.stale.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-2">
                <span>
                  {t('class.beat.staleRow', { song: row.song, beats: beatsInWords(row.beats) })}
                </span>
                <Button
                  variant="ghost"
                  aria-label={t('class.beat.clearAria', { song: row.song })}
                  disabled={change.busy}
                  onClick={() => {
                    clearRow(row.id)
                  }}
                >
                  {t('class.beat.clear')}
                </Button>
              </li>
            ))}
          </ul>
          <p className="m-0">
            <Button variant="secondary" disabled={change.busy} onClick={clearAll}>
              {t('class.beat.clearAll')}
            </Button>
          </p>
        </div>
      ) : null}
      {view.pending ? (
        <div className="notice" data-kind="info" role="status" data-testid="beat-one-pending">
          <p>
            {t('class.beat.pending')}
          </p>
          <p>
            <Button disabled={change.busy || apply.busy || busy} onClick={applyFixes}>
              {t('class.beat.apply')}
            </Button>
          </p>
          {busy ? <p className="muted">{t('class.waitRunning')}</p> : null}
        </div>
      ) : null}
      {apply.error ? <Notice kind="error">{apply.error}</Notice> : null}
    </section>
  )
}
