import { useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { classReasonText, isStale } from '../../api/classMessages'
import {
  clearBeatOne,
  setBeatOne,
  type BeatOne,
  type BeatOneSong,
  type ClassSummary,
} from '../../api/classes'
import { getConnections } from '../../api/connections'
import { createRefitJob } from '../../api/jobs'
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
  if (beats === 0) return 'as analysed'
  const n = Math.abs(beats)
  return `${String(n)} beat${n === 1 ? '' : 's'} ${beats > 0 ? 'later' : 'earlier'}`
}

function songName(song: BeatOneSong): string {
  return song.title !== null && song.title !== '' ? song.title : `Song ${String(song.song)}`
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
  const [view, setView] = useState<BeatOne>(summary.beatOne)
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
      className="panel flex flex-col gap-3"
      aria-labelledby="beat-one-title"
      data-testid="beat-one"
    >
      <h2 id="beat-one-title" className="panel__title m-0">
        Beat 1
      </h2>
      <p className="m-0">
        Listen to the class. If the changes in a song land a beat early, move its beat 1 later; if
        they land late, move it earlier. A bar has four beats, so one step is a quarter of a bar.
      </p>
      <ol className="m-0 flex list-none flex-col gap-3 p-0">
        {view.songs.map((song) => {
          const name = songName(song)
          return (
            <li
              key={song.song}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line pb-3"
              data-testid={`beat-one-song-${String(song.song)}`}
            >
              <div className="min-w-0 grow basis-48">
                <p className="m-0 font-medium">{name}</p>
                <p className="m-0 text-muted text-[0.875rem]">
                  From {clock(song.start)}, {String(Math.round(song.bpm))} BPM
                </p>
                {song.estimate && song.beats === 0 ? (
                  <p className="m-0 text-muted text-[0.875rem]">
                    Beat 1 is an estimate here: listen to the first change in this song.
                  </p>
                ) : null}
              </div>
              <div className="flex items-center gap-2" role="group" aria-label={`Beat 1 of ${name}`}>
                <Button
                  variant="secondary"
                  aria-label={`Move beat 1 of ${name} earlier`}
                  disabled={change.busy || song.beats <= -MAX_BEATS}
                  onClick={() => {
                    setTo(song, song.beats - 1)
                  }}
                >
                  Earlier
                </Button>
                <output aria-live="polite" className="min-w-32 text-center">
                  {beatsInWords(song.beats)}
                </output>
                <Button
                  variant="secondary"
                  aria-label={`Move beat 1 of ${name} later`}
                  disabled={change.busy || song.beats >= MAX_BEATS}
                  onClick={() => {
                    setTo(song, song.beats + 1)
                  }}
                >
                  Later
                </Button>
                <Button
                  variant="ghost"
                  aria-label={`Reset beat 1 of ${name}`}
                  disabled={change.busy || song.beats === 0}
                  onClick={() => {
                    setTo(song, 0)
                  }}
                >
                  Reset
                </Button>
              </div>
            </li>
          )
        })}
      </ol>
      {change.error ? <Notice kind="error">{change.error}</Notice> : null}
      {view.stale > 0 ? (
        <p className="m-0 text-muted" data-testid="beat-one-stale">
          {view.stale === 1
            ? 'One timing fix was set on earlier music and is not used.'
            : `${String(view.stale)} timing fixes were set on earlier music and are not used.`}
        </p>
      ) : null}
      {view.pending ? (
        <div className="notice" data-kind="info" role="status" data-testid="beat-one-pending">
          <p>
            This version does not use your timing fixes yet. Apply them to make a new version with
            the changes moved onto the corrected beats; save or update it in MOWL after that.
          </p>
          <p>
            <Button disabled={apply.busy || busy} onClick={applyFixes}>
              Apply timing fixes
            </Button>
          </p>
          {busy ? <p className="muted">Wait for the running task to finish first.</p> : null}
        </div>
      ) : null}
      {apply.error ? <Notice kind="error">{apply.error}</Notice> : null}
    </section>
  )
}
