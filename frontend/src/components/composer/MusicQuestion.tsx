import { useState } from 'react'
import { putMusicAnswer, type MusicAnswer } from '../../api/musicAnswer'
import { Trans, useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { MusicTagRow } from '../start/MusicTagRow'
import { NO_TAGS, attachedPlaylists, togglePlaylist, type PlaylistTag } from '../start/musicTags'
import { TextInput } from '../newclass/inputs'
import { Button } from '../ui/button'
import { musicAnswerError } from './musicAnswerError'

/** The longest note the server takes (`PUT /api/classes/{id}/music`). */
const MAX_NOTE = 200

type Mode = 'describe' | 'playlists' | null

/**
 * The music question beside the sketch (ADR 0038 amendment of 2026-11-24, decision 5), shown while the class's
 * music is undecided. Every answer is stored on the class and starts nothing: the song search waits for the
 * answer, Spotify and the consent. Without Spotify the options stay, with a link to connect it.
 * "Continue without music" is shown but off until the `soundless_enabled` flag is on.
 */
export function MusicQuestion({
  classId,
  offered,
  connected,
  soundlessEnabled,
  canStartWithout,
  onAnswered,
  onLater,
  onWithoutMusic,
  withoutError,
  withoutBusy,
}: {
  classId: string
  offered: readonly PlaylistTag[]
  /** Spotify is connected (or not yet known): false shows the connect link. */
  connected: boolean
  soundlessEnabled: boolean
  /** The request's words and a MOWL connection are known, so "Continue without music" can start. */
  canStartWithout: boolean
  /** The answer was stored: read the class again. */
  onAnswered: () => void
  /** "Decide later" was stored: close the card for now. */
  onLater: () => void
  onWithoutMusic: () => void
  withoutError: string | null
  withoutBusy: boolean
}) {
  const t = useT()
  const action = useAction()
  const [mode, setMode] = useState<Mode>(null)
  const [note, setNote] = useState('')
  const [tags, setTags] = useState(NO_TAGS)

  const send = (answer: MusicAnswer) =>
    void action.run(
      async () => {
        await putMusicAnswer(classId, answer)
        if (answer.choice === 'later') onLater()
        else onAnswered()
      },
      (error) => musicAnswerError(t, error),
    )
  const chosen = attachedPlaylists(tags)
  const busy = action.busy || withoutBusy
  const shown = action.error ?? withoutError
  return (
    <section aria-label={t('cw.music.title')} data-testid="music-question" className="flex flex-col gap-3 rounded-2xl border border-line bg-raised p-4">
      <p className="m-0 text-[0.9375rem]">{t('cw.music.question')}</p>
      {!connected ? (
        <p className="m-0 text-[0.9375rem] text-muted" data-testid="music-needs-spotify">
          <Trans id="music.needsSpotify" slots={{ link: <Link to="/account/spotify">{t('music.connectSpotify')}</Link> }} />
        </p>
      ) : null}
      <div role="group" aria-label={t('cw.music.answers')} className="flex flex-wrap gap-2">
        <Button
          disabled={busy}
          onClick={() => {
            send({ choice: 'pick' })
          }}
        >
          {t('cw.music.pick')}
        </Button>
        <Button
          variant="secondary"
          disabled={busy}
          aria-expanded={mode === 'describe'}
          onClick={() => {
            setMode(mode === 'describe' ? null : 'describe')
          }}
        >
          {t('cw.music.describe')}
        </Button>
        <Button
          variant="secondary"
          disabled={busy}
          aria-expanded={mode === 'playlists'}
          onClick={() => {
            setMode(mode === 'playlists' ? null : 'playlists')
          }}
        >
          {t('cw.music.playlists')}
        </Button>
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() => {
            send({ choice: 'later' })
          }}
        >
          {t('cw.music.later')}
        </Button>
      </div>
      {mode === 'describe' ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (note.trim() !== '') send({ choice: 'describe', note })
          }}
        >
          <TextInput
            label={t('cw.music.note.label')}
            placeholder={t('cw.music.note.placeholder')}
            maxLength={MAX_NOTE}
            value={note}
            onChange={(e) => {
              setNote(e.target.value)
            }}
          />
          <Button type="submit" disabled={busy || note.trim() === ''} className="self-start">
            {t('cw.music.note.send')}
          </Button>
        </form>
      ) : null}
      {mode === 'playlists' ? (
        <div className="flex flex-col gap-2">
          <p className="m-0 text-[0.875rem] text-muted">{t('cw.music.playlists.hint')}</p>
          <MusicTagRow
            offered={offered}
            tags={tags}
            noMusicOffered={false}
            onTogglePlaylist={(tag) => {
              setTags(togglePlaylist(tags, tag))
            }}
            onToggleNone={() => undefined}
          />
          <Button
            disabled={busy || chosen.length === 0}
            className="self-start"
            onClick={() => {
              send({ choice: 'playlists', playlistIds: chosen.map((tag) => tag.id) })
            }}
          >
            {t('cw.music.playlists.send')}
          </Button>
        </div>
      ) : null}
      <div className="flex flex-col items-start gap-1">
        <Button variant="secondary" disabled={busy || !soundlessEnabled || !canStartWithout} onClick={onWithoutMusic}>
          {t('cw.music.none')}
        </Button>
        {!soundlessEnabled ? <span className="text-[0.8125rem] text-muted">{t('cw.music.noneSoon')}</span> : null}
      </div>
      {shown !== null ? <Notice kind="error">{shown}</Notice> : null}
    </section>
  )
}
