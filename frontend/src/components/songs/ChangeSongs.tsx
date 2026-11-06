import { useEffect, useRef, useState } from 'react'
import type { ClassSummary } from '../../api/classes'
import { startError } from '../../api/songMessages'
import { createReviseSongsJob, getSongList, type SongList } from '../../api/songs'
import { useT } from '../../i18n/I18n'
import { currentConnectionIds, digest, isNotConnected } from '../../lib/songs'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { SongRows } from './SongRows'

const MAX_REQUEST = 2000
/** The id of the panel: "Change songs" in the chat's answer to a songs-only request scrolls here. */
export const CHANGE_SONGS_ID = 'change-songs'

/**
 * Change songs (ADR 0008 section 6): for a class whose songs Claude picked, mark the songs to
 * replace (or all of them), say what is wanted, and start a `revise_songs` job from the current
 * version. The new list comes back for approval like the first one.
 */
export function ChangeSongs({ summary }: { summary: ClassSummary }) {
  const t = useT()
  const revisionId = summary.songs.revisionId
  const [list, setList] = useState<SongList | null>(null)
  const [failed, setFailed] = useState(false)
  const [marks, setMarks] = useState<ReadonlySet<number>>(new Set())
  const [request, setRequest] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const action = useAction()
  const locked = useRef(false)
  const { navigate } = useRouter()
  // A soundless class has no songs to change (#530).
  const eligible = !summary.soundless && summary.songs.state === 'approved' && revisionId !== null

  useEffect(() => {
    if (!eligible || revisionId === null) return
    const abort = new AbortController()
    getSongList(summary.id, revisionId, abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setList(found)
      },
      () => {
        if (!abort.signal.aborted) setFailed(true)
      },
    )
    return () => {
      abort.abort()
    }
  }, [eligible, summary.id, revisionId])

  if (!eligible) return null
  const base = summary.currentRevisionId
  const busy = summary.openJob !== null
  const toggle = (position: number) => {
    setMarks((previous) => {
      const next = new Set(previous)
      if (!next.delete(position)) next.add(position)
      return next
    })
  }

  const submit = (all: boolean) => {
    const text = request.trim()
    if (text === '') {
      setProblem(t('songs.change.writeRequest'))
      return
    }
    if (text.length > MAX_REQUEST) {
      setProblem(t('songs.change.tooLong', { max: MAX_REQUEST }))
      return
    }
    if (!all && marks.size === 0) {
      setProblem(t('songs.change.markSongs'))
      return
    }
    setProblem(null)
    if (base === null || locked.current) return
    locked.current = true
    const replace = all ? ('all' as const) : [...marks].sort((a, b) => a - b).map((p) => p - 1)
    const scope = `songs.${summary.id}.${base}.${digest(`${JSON.stringify(replace)}|${text}`)}`
    void action
      .run(
        async () => {
          const ids = await currentConnectionIds()
          const job = await createReviseSongsJob(
            {
              class_id: summary.id,
              base_revision_id: base,
              replace,
              request: text,
              mode: 'pick',
              mowl_connection_id: ids.mowl,
              spotify_connection_id: ids.spotify,
            },
            actionKey(scope),
          )
          dropActionKey(scope)
          navigate(`/jobs/${job.id}`)
        },
        (error) =>
          isNotConnected(error)
            ? t('songs.change.notConnected')
            : startError(error),
      )
      .finally(() => {
        locked.current = false
      })
  }

  return (
    <Card
      id={CHANGE_SONGS_ID}
      tabIndex={-1}
      className="flex scroll-mt-4 flex-col gap-3"
      aria-labelledby="change-songs-title"
      role="region"
    >
      <CardTitle id="change-songs-title">{t('songs.change.title')}</CardTitle>
      {busy ? <p className="m-0 text-muted">{t('songs.change.wait')}</p> : null}
      {failed ? <Notice kind="error">{t('songs.change.loadFailed')}</Notice> : null}
      {list === null && !failed ? <p className="m-0 text-muted">{t('songs.change.loading')}</p> : null}
      {list !== null ? (
        <>
          <p className="m-0 text-muted">
            {t('songs.change.intro')}
          </p>
          <SongRows songs={list.songs} marks={marks} onToggle={toggle} disabled={busy || action.busy} />
          <label htmlFor="songs-request" className="text-[0.9375rem] font-medium text-text">
            {t('songs.change.different')}
          </label>
          <textarea
            id="songs-request"
            className="min-h-20 rounded-md border border-line bg-raised p-3 text-text"
            maxLength={MAX_REQUEST}
            value={request}
            onChange={(e) => {
              setRequest(e.target.value)
            }}
          />
          {problem !== null ? <Notice kind="error">{problem}</Notice> : null}
          {action.error ? <Notice kind="error">{action.error}</Notice> : null}
          <div className="flex flex-wrap gap-3">
            <Button
              disabled={busy || action.busy}
              onClick={() => {
                submit(false)
              }}
            >
              {t('songs.change.marked')}
            </Button>
            <Button
              variant="secondary"
              disabled={busy || action.busy}
              onClick={() => {
                submit(true)
              }}
            >
              {t('songs.change.all')}
            </Button>
          </div>
        </>
      ) : null}
    </Card>
  )
}
