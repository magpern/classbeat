import { useState } from 'react'
import {
  checkAgain,
  confirmMix,
  createAnotherAnyway,
  type OutputStatus,
} from '../../api/studioOutputStates'
import type { Output } from '../../api/studioOutputs'
import { studioError } from '../../api/studioSongs'
import { useT } from '../../i18n/I18n'
import type { CatalogueKey } from '../../i18n/en'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'

const STATE_TEXT = {
  pending: 'studio.os.state.pending',
  running: 'studio.os.state.running',
  done: 'studio.os.state.done',
  failed: 'studio.os.state.failed',
  in_doubt: 'studio.os.state.in_doubt',
  published_not_listed: 'studio.os.state.published_not_listed',
} as const satisfies Record<OutputStatus['state'], CatalogueKey>

const NOTE_TEXT = {
  created_not_public: 'studio.os.note.created_not_public',
  not_sent: 'studio.os.note.not_sent',
} as const satisfies Record<string, CatalogueKey>

const CATEGORY_TEXT: Record<string, CatalogueKey> = {
  writes_off: 'studio.os.category.writes_off',
  needs_reconnect: 'studio.os.category.needs_reconnect',
  rate_limited: 'studio.os.category.rate_limited',
  track_unavailable: 'studio.os.category.track_unavailable',
  track_too_large: 'studio.os.category.track_too_large',
  upstream_unavailable: 'studio.os.category.upstream_unavailable',
  app_misconfigured: 'studio.os.category.upstream_unavailable',
  invalid_response: 'studio.os.category.upstream_unavailable',
  connection_changed: 'studio.os.category.connection_changed',
  render_not_reproducible: 'studio.os.category.render_not_reproducible',
  playlist_has_repeats: 'studio.os.category.playlist_has_repeats',
  music_changed: 'studio.os.category.music_changed',
  not_connected: 'studio.os.category.not_connected',
  invalid_input: 'studio.os.category.invalid_input',
  analysis_failed: 'studio.os.category.analysis_failed',
  internal: 'studio.os.category.internal',
}

/** Why Check again could not settle an output in doubt (backend `check_reason`, #1117). */
const CHECK_REASON_TEXT: Record<string, CatalogueKey> = {
  connection_changed: 'studio.os.checkReason.connection_changed',
}

/** Failures that approving again cannot help: the category's own sentence says what to do. */
const NO_RETRY: readonly string[] = [
  'render_not_reproducible',
  'music_changed',
  'analysis_failed',
  'invalid_input',
  'playlist_has_repeats',
]

const DUPLICATE_RISK = {
  spotify_playlist: 'studio.os.duplicateRisk.spotify_playlist',
  swedebeat_playlist: 'studio.os.duplicateRisk.swedebeat_playlist',
  swedebeat_mix: 'studio.os.duplicateRisk.swedebeat_mix',
  mowl: 'studio.os.duplicateRisk.mowl',
} as const satisfies Record<Output, CatalogueKey>

const SUPERSEDED_HINT = {
  spotify_playlist: 'studio.os.supersededHint.spotify_playlist',
  swedebeat_playlist: 'studio.os.supersededHint.swedebeat',
  swedebeat_mix: 'studio.os.supersededHint.swedebeat',
  mowl: 'studio.os.supersededHint.mowl',
} as const satisfies Record<Output, CatalogueKey>

function minutes(sec: number): string {
  const whole = Math.round(sec)
  return `${String(Math.floor(whole / 60))}:${String(whole % 60).padStart(2, '0')}`
}

/**
 * One output's state in words and the actions that are safe for it. "Check again" and "It is my
 * mix" never create anything. "Create another anyway" first shows the duplicate risk (SwedeBeat
 * has no delete) and posts only after the user ticks that they accept it.
 */
function OutputRow({
  classId,
  status,
  onChange,
}: {
  classId: string
  status: OutputStatus
  onChange: (next: OutputStatus) => void
}) {
  const t = useT()
  const action = useAction()
  const [asking, setAsking] = useState(false)
  const [accepted, setAccepted] = useState(false)
  const scope = (name: string) => `studio-output.${status.revisionId}.${status.output}.${String(status.attempt)}.${name}`
  const has = (a: OutputStatus['actions'][number]) => status.actions.includes(a)

  const run = (name: string, call: (key: string) => Promise<OutputStatus>) => {
    void action.run(async () => {
      const next = await call(actionKey(scope(name)))
      dropActionKey(scope(name))
      setAsking(false)
      setAccepted(false)
      onChange(next)
    }, studioError)
  }

  const noteText =
    status.note === 'created_not_public' || status.note === 'not_sent' ? t(NOTE_TEXT[status.note]) : null
  const category = status.state === 'failed' && status.category !== null ? CATEGORY_TEXT[status.category] : undefined

  const checkReason =
    status.state === 'in_doubt' && status.checkReason !== null ? CHECK_REASON_TEXT[status.checkReason] : undefined

  return (
    <li className="rounded-md border border-line bg-surface p-3" data-testid={`studio-output-state-${status.output}`}>
      <p className="m-0 font-medium text-text">{t(`studio.out.${status.output}`)}</p>
      <p className="m-0 text-text">{status.superseded ? t('studio.os.superseded') : t(STATE_TEXT[status.state])}</p>
      {noteText !== null && !status.superseded ? <p className="m-0 text-muted">{noteText}</p> : null}
      {status.name !== null && status.state !== 'done' ? (
        <p className="m-0 text-muted">{t('studio.os.name', { name: status.name })}</p>
      ) : null}
      {category !== undefined ? <p className="m-0 text-muted">{t(category)}</p> : null}
      {checkReason !== undefined ? <p className="m-0 text-muted">{t(checkReason)}</p> : null}
      {status.candidate !== null && status.state === 'in_doubt' ? (
        <p className="m-0 text-text">
          {status.candidate.durationSec === null
            ? t('studio.os.candidateNoLength', { name: status.candidate.name })
            : t('studio.os.candidate', { name: status.candidate.name, length: minutes(status.candidate.durationSec) })}
        </p>
      ) : null}
      {status.superseded ? (
        <p className="m-0 text-muted">{t(SUPERSEDED_HINT[status.output])}</p>
      ) : has('retry') && !(status.category !== null && NO_RETRY.includes(status.category)) ? (
        <p className="m-0 text-muted">{t('studio.os.retryHint')}</p>
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <div className="mt-2 flex flex-wrap gap-2">
        {has('confirm') ? (
          <Button disabled={action.busy} onClick={() => { run('confirm', (key) => confirmMix(classId, status.revisionId, key)) }}>
            {t('studio.os.confirm')}
          </Button>
        ) : null}
        {has('check_again') ? (
          <Button disabled={action.busy} onClick={() => { run('check', (key) => checkAgain(classId, status.revisionId, status.output, key)) }}>
            {t('studio.os.checkAgain')}
          </Button>
        ) : null}
        {has('create_another') && !asking ? (
          <Button disabled={action.busy} onClick={() => { setAsking(true) }}>
            {t('studio.os.createAnother')}
          </Button>
        ) : null}
      </div>
      {asking ? (
        <div className="mt-2 flex flex-col gap-2" data-testid="studio-duplicate-warning" role="group" aria-label={t('studio.os.createAnother')}>
          <p className="m-0 text-text">{t(DUPLICATE_RISK[status.output])}</p>
          <label className="flex min-h-11 cursor-pointer items-start gap-3">
            <input
              type="checkbox"
              className="mt-1"
              checked={accepted}
              onChange={(event) => { setAccepted(event.target.checked) }}
            />
            <span>{t(status.output.startsWith('swedebeat') ? 'studio.os.duplicateAccept.swedebeat' : 'studio.os.duplicateAccept')}</span>
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={!accepted || action.busy}
              onClick={() => { run('another', (key) => createAnotherAnyway(classId, status.revisionId, status.output, key)) }}
            >
              {t('studio.os.createAnotherConfirm')}
            </Button>
            <Button disabled={action.busy} onClick={() => { setAsking(false); setAccepted(false) }}>
              {t('studio.os.cancel')}
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  )
}

/** The states of the outputs made from this class's drafts; nothing when none was started. */
export function StudioOutputStates({
  classId,
  outputs,
  onChange,
}: {
  classId: string
  outputs: readonly OutputStatus[]
  onChange: (next: OutputStatus) => void
}) {
  const t = useT()
  if (outputs.length === 0) return null
  return (
    <section className="flex flex-col gap-2" aria-labelledby="studio-os-title" data-testid="studio-output-states">
      <h3 id="studio-os-title" className="m-0 font-display text-lg font-bold">
        {t('studio.os.title')}
      </h3>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {outputs.map((status) => (
          <OutputRow key={`${status.revisionId}.${status.output}`} classId={classId} status={status} onChange={onChange} />
        ))}
      </ul>
    </section>
  )
}
