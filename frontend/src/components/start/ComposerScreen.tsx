import { ArrowRight, Shuffle } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { createInterpretJob } from '../../api/interpret'
import { useLocale, useT } from '../../i18n/I18n'
import { forgetExample } from '../../lib/exampleHandoff'
import { chooseSport, followText } from '../../lib/sportHint'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { MAX_REQUEST, recallRequest, rememberRequest } from '../../lib/startRequest'
import { exampleKey, type ExampleSlug } from '../../publicSite/examples'
import { Link, useQuery, useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { createError } from '../newclass/createError'
import { TextArea } from '../newclass/inputs'
import type { ReadySource } from '../newclass/SourceGate'
import { usePlanAllowance } from '../newclass/usePlanAllowance'
import { PlanNudge } from '../plans/PlanNudge'
import { UpgradeLink } from '../plans/UpgradeLink'
import { Button } from '../ui/button'
import { BriefFrame, BRIEF_AREA } from './BriefFrame'
import { ClassesLeft } from './ClassesLeft'
import { PlanLength } from './PlanLength'
import { FieldCard } from './FieldCard'
import { composerInput, initialDraft, keptFromRequest, type ComposerRequest, type Sport } from './composerModel'
import { MusicTagRow } from './MusicTagRow'
import { forSport, toggleNone, togglePlaylist, type PlaylistTag } from './musicTags'
import { SpotifyPrompt } from './SpotifyPrompt'
import { composerSpotifyGate } from './composerSpotifyGate'
import { spotifyConnectionId, type SpotifyGate } from './spotifyGate'
import { pickSurprise } from './surprise'

/**
 * `/classes/new` with the `composer_flow` flag on (ADR 0038 section 10.1): one card with the words and the
 * attached-music tags, the sport select (always: running is always available, #1544; it follows the words
 * until the instructor picks a sport, `lib/sportHint`), Surprise me and Start. Start sends an
 * `interpret_request` job and goes to the class. Nothing here is required but the words. The flag off keeps
 * `StartScreen`.
 */
export function ComposerScreen({ source, example = null }: { source: ReadySource; example?: ExampleSlug | null }) {
  const t = useT()
  const { locale } = useLocale()
  const { navigate } = useRouter()
  const from = useQuery().get('from')
  const action = useAction()
  const { allowed, plan, classesLeft, settled } = usePlanAllowance()
  const offered = useMemo<PlaylistTag[]>(
    () => source.playlists.map((p) => ({ id: p.spotifyPlaylistId, name: p.name })),
    [source.playlists],
  )
  // A request kept for `from` (the "Try again" hand-off) wins over an example, which then stays kept for a day.
  const kept = useMemo(() => (from === null ? null : recallRequest(from)), [from])
  const usedExample = kept === null ? example : null
  const [draft] = useState(() =>
    initialDraft({
      kept,
      exampleWords: usedExample === null ? null : t(exampleKey(usedExample, 'prompt')),
      offered,
      noMusicOffered: true, // "No music" is always a choice (#1543); running drops it below
    }),
  )
  const [request, setRequest] = useState(draft.request)
  // What the instructor picked. A running class has playlists only, so "No music" is hidden while it is running
  // but kept here: a flip back to cycling, by the words or by hand, brings it back (#1558).
  const [picked, setPicked] = useState(draft.tags)
  const [toggle, setToggle] = useState(draft.sport)
  const sport: Sport = toggle.sport
  const [problem, setProblem] = useState<string | null>(null)
  // The server refused for want of Spotify: the same prompt as the gate's, instead of an error.
  const [refusedForSpotify, setRefusedForSpotify] = useState<SpotifyGate['need'] | null>(null)
  const running = sport === 'running'
  const tags = forSport(picked, running)
  const noMusicOffered = !running
  const promptRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (usedExample === null) return
    forgetExample()
    promptRef.current?.focus()
  }, [usedExample])
  // One key per distinct request: a repeated click or a retry after a lost answer returns the same job.
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const spotify = spotifyConnectionId(source.songs)
  // Songs need Spotify, but the composer asks about the music after the sketch (ADR 0038 amendment of
  // 2026-11-24): a request with the music left open is not blocked, it only says so. Attached playlists still are.
  const { gate, songsLater } = composerSpotifyGate(source.songs, source.swedebeat, tags, running)

  const start = (text: string) => {
    const words = text.trim()
    if (words === '') {
      setProblem(t('start.error.empty'))
      return
    }
    if (gate?.blocking) return
    setProblem(null)
    setRefusedForSpotify(null)
    const sent: ComposerRequest = {
      words,
      mowlConnectionId: source.connection.id,
      spotifyConnectionId: spotify,
      tags,
      sport,
      sportByHand: toggle.manual,
    }
    const input = composerInput(sent)
    const fingerprint = JSON.stringify(input)
    if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, key: newIdempotencyKey() }
    const key = attempt.current.key
    void action.run(
      async () => {
        const job = await createInterpretJob(input, key)
        rememberRequest(job.classId, keptFromRequest(sent))
        navigate(`/classes/${job.classId}`)
      },
      (error) => {
        if (error instanceof ApiError && error.reason === 'needs_spotify') setRefusedForSpotify('connect')
        if (error instanceof ApiError && error.reason === 'song_picking_unavailable') setRefusedForSpotify('unavailable')
        return createError(t, error)
      },
    )
  }
  // "Surprise me" starts at once, as if the words had been typed: they are shown in the box and sent as they are.
  const surprise = () => {
    const words = pickSurprise(locale, allowed?.maxMinutes ?? null, sport)
    setRequest(words)
    start(words)
  }
  // While the instructor types, the select follows the words; a sport picked by hand stays (#1544).
  const typed = (text: string) => {
    setRequest(text)
    const next = followText(toggle, text)
    if (next === toggle) return
    setToggle(next)
  }

  const shown = problem ?? (refusedForSpotify !== null ? null : action.error)
  const busy = action.busy || gate?.blocking === true
  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault()
        start(request)
      }}
      noValidate
      aria-label={t('start.formLabel')}
    >
      {shown ? <Notice kind="error">{shown}</Notice> : null}
      {problem === null && action.upgrade ? <UpgradeLink /> : null}
      <PlanNudge trigger="low" plan={plan} />
      <BriefFrame>
        <TextArea
          ref={promptRef}
          label={t('start.prompt.label')}
          hint={t('composer.intro')}
          placeholder={t('start.prompt.placeholder')}
          rows={4}
          maxLength={MAX_REQUEST}
          value={request}
          className={BRIEF_AREA}
          onChange={(e) => {
            typed(e.target.value)
          }}
        />
      </BriefFrame>
      <div className="grid gap-3.5 sm:grid-cols-2">
        <FieldCard title={t('start.length.title')}>
          <PlanLength allowed={allowed} settled={settled} />
          <select
            aria-label={t('newclass.sport')}
            value={sport}
            onChange={(e) => {
              const next: Sport = e.target.value === 'running' ? 'running' : 'cycling'
              setToggle(chooseSport(next))
            }}
            className="min-h-11 cursor-pointer self-start rounded-full border border-line bg-raised px-4 text-[0.9375rem] text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
          >
            <option value="cycling">{t('newclass.sport.cycling')}</option>
            <option value="running">{t('newclass.sport.running')}</option>
          </select>
        </FieldCard>
        <FieldCard title={t('start.music.from')} className="overflow-visible">
          <MusicTagRow
            offered={offered}
            tags={tags}
            noMusicOffered={noMusicOffered}
            onTogglePlaylist={(tag) => {
              setPicked(togglePlaylist(tags, tag))
            }}
            onToggleNone={() => {
              setPicked(toggleNone(tags))
            }}
          />
          {gate !== null ? (
            <SpotifyPrompt need={gate.need} soundless={noMusicOffered} />
          ) : refusedForSpotify !== null ? (
            <SpotifyPrompt need={refusedForSpotify} soundless={noMusicOffered} />
          ) : songsLater ? (
            <Notice>{t('composer.spotify.later')}</Notice>
          ) : null}
        </FieldCard>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={busy} className="min-h-14 px-8 text-[1.0625rem] font-bold">
          {t('composer.start')}
          <ArrowRight aria-hidden="true" size={16} />
        </Button>
        <Button variant="secondary" disabled={busy} onClick={surprise}>
          <Shuffle aria-hidden="true" size={16} />
          {t('composer.surprise')}
        </Button>
        <ClassesLeft count={classesLeft} />
      </div>
      <p className="m-0 text-[0.875rem] text-muted">
        {t('composer.note')} <Link to="/classes/new/advanced">{t('composer.advanced')}</Link>
      </p>
    </form>
  )
}
