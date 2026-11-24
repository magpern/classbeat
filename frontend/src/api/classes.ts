import { del, get, post, request } from './client'
import {
  count,
  isObject,
  list,
  object,
  optionalCount,
  optionalFlag,
  optionalText,
  text,
} from './decode'
import { decodeBrief, decodeMatchPointer, decodeProfile } from './classBrief'
import { decodeBeatOne, type BeatOne } from './beatOneTypes'
import { decodeComposerState, type ComposerState } from './composerState'
import { decodePlaylist, decodeSongs, type ClassPlaylist, type ClassSongs } from './classSongs'
import { decodeDraftContent, decodeUnknownBpm } from './draftTypes'
import { decodeInterpretationItems } from './interpretationItems'
import type { Brief } from './jobs'
import {
  decodeChange,
  decodeRevisionSummary,
  decodeRuleHits,
  type ChangeSummary,
  type Revision,
  type RevisionSummary,
} from './revisionTypes'

export * from './beatOneTypes'
export * from './revisionTypes'
export * from './classSongs'
export * from './beatOneCalls'

// The class API (ADR 0007 section 12, backend/src/classbeat/classes/routes.py). Server text is
// data: decoded into typed fields and rendered as text by React, never as HTML.

export interface OpenJob {
  id: string
  /** `design_draft`, `revise_draft`, ... */
  kind: string
  state: string
}

export interface SavedSummary {
  count: number
  latestRevisionNumber: number | null
  latestSavedAt: string | null
  latestUpToDate: boolean | null
  /**
   * NOT SENT by the backend yet (PR for #150 lists the gap): the id Update names
   * (`saved_workout_id`, ADR 0007 section 5) and how many saves were detached by a MOWL
   * reconnect (section 9). Absent means null and 0; the page then offers no Update.
   */
  latestSavedWorkoutId: string | null
  detachedCount: number
  /**
   * SL6 (#532): whether the newest saved row was read back from MOWL and matched (`state_hash` set).
   * False: MOWL took the write but classbeat could not confirm it (ADR 0007 item 8). Null: no saved
   * row, or an answer without the field; neither is ever worded as a match.
   */
  latestVerified: boolean | null
}

export interface ClassPlayer {
  sentRevisionId: string
  sentRevisionNumber: number
}

export function decodeClassPlayer(value: unknown): ClassPlayer | null {
  if (!isObject(value)) return null
  const item = object(value, 'player')
  return { sentRevisionId: text(item, 'sent_revision_id'), sentRevisionNumber: count(item, 'sent_revision_number') }
}

/** The answer of the class's deletion: whether a removal job went to the player, and its id. */
export interface Deleted {
  mowlWorkoutsKept: number
  playerRemovalJobId: string | null
}

export function decodeDeleted(value: unknown): Deleted {
  const body = object(value, 'deleted')
  const started = optionalFlag(body, 'player_removal_started') === true
  return {
    mowlWorkoutsKept: count(body, 'mowl_workouts_kept'),
    playerRemovalJobId: started ? optionalText(body, 'player_removal_job_id') : null,
  }
}

export interface ClassSummary {
  id: string
  title: string
  /** ADR 0018 section 1: the class's sport; an answer without it is cycling. */
  sport: 'cycling' | 'running'
  /** ADR 0007 amendment item 10 (#530): a class with no music; an answer without it is not. */
  soundless: boolean
  /** ADR 0036 section 5 (#1173): the class's stored music is the SwedeBeat studio's; an answer without it is not. */
  studio: boolean
  /** #1453: the class's stored music is a Spotify playlist draft (not a SwedeBeat mix); an answer without it is not. */
  spotify: boolean
  createdAt: string
  updatedAt: string
  currentRevisionId: string | null
  currentRevisionNumber: number | null
  revisionCount: number
  saved: SavedSummary
  openJob: OpenJob | null
  /** The current version's intervals as [zone, seconds], in order (empty without one). */
  profile: [number, number][]
  /** ADR 0012 section 7: the backend's flag, set when the current version has a zone 7 interval. */
  tssApproximate: boolean
  /** The brief the class was designed for; null when there is none (or on the list). */
  brief: Brief | null
  /** #1234: the user's own request text for the class; null when there is none. */
  request: string | null
  /** #1234: how many songs the current version plays; null for a soundless class or before songs exist. */
  songCount: number | null
  /** Only the class page's answer has these; the list's rows read as none. */
  songs: ClassSongs
  playlist: ClassPlaylist | null
  /** ADR 0016: false while the `swedebeat_enabled` flag is off (the key is absent from the answer). */
  swedebeatOn: boolean
  /** The class's newest Swedebeat match and whether it is the approved selection. */
  swedebeatMatch: { revisionId: string; approved: boolean } | null
  /** Beat-one corrections (ADR 0007 amendment): unavailable on the list and without beat data. */
  beatOne: BeatOne
  /** What the player holds (#1068); null: never sent, or not connected. */
  player: ClassPlayer | null
  /** A SwedeBeat playlist or mix was written for the class; a class delete cannot take it back. */
  swedebeatWritten: boolean
  /** The composer flow's stored state (ADR 0038 sections 5 and 7); empty without the flag. */
  composer: ComposerState
  /** #1484: whether a chat change is offered now, as the history says it; null when the answer has none (the list). */
  canChange: boolean | null
}

function decodeClass(value: unknown): ClassSummary {
  const item = object(value, 'class')
  const saved = object(item.saved, 'saved')
  const job = item.open_job
  let openJob: OpenJob | null = null
  if (job !== null && job !== undefined) {
    const found = object(job, 'open_job')
    openJob = { id: text(found, 'id'), kind: text(found, 'kind'), state: text(found, 'state') }
  }
  return {
    id: text(item, 'id'),
    title: text(item, 'title'),
    sport: item.sport === 'running' ? 'running' : 'cycling',
    soundless: item.soundless === true,
    studio: item.studio === true,
    spotify: item.spotify === true,
    createdAt: text(item, 'created_at'),
    updatedAt: text(item, 'updated_at'),
    currentRevisionId: optionalText(item, 'current_revision_id'),
    currentRevisionNumber: optionalCount(item, 'current_revision_number'),
    revisionCount: count(item, 'revision_count'),
    saved: {
      count: count(saved, 'count'),
      latestRevisionNumber: optionalCount(saved, 'latest_revision_number'),
      latestSavedAt: optionalText(saved, 'latest_saved_at'),
      latestUpToDate: optionalFlag(saved, 'latest_up_to_date'),
      latestSavedWorkoutId: optionalText(saved, 'latest_saved_workout_id'),
      detachedCount: optionalCount(saved, 'detached_count') ?? 0,
      latestVerified: optionalFlag(saved, 'latest_verified'),
    },
    openJob,
    profile: decodeProfile(item.profile),
    tssApproximate: optionalFlag(item, 'tss_approximate') ?? false,
    brief: decodeBrief(item.brief),
    request: optionalText(item, 'request'),
    songCount: optionalCount(item, 'song_count'),
    songs: decodeSongs(item.songs),
    playlist: decodePlaylist(item.playlist),
    swedebeatOn: 'swedebeat_match' in item,
    swedebeatMatch: decodeMatchPointer(item.swedebeat_match),
    beatOne: decodeBeatOne(item.beat_one),
    player: decodeClassPlayer(item.player),
    swedebeatWritten: item.swedebeat_written === true,
    composer: decodeComposerState(item),
    canChange: optionalFlag(item, 'can_change'),
  }
}

const base = (classId: string) => `/api/classes/${encodeURIComponent(classId)}`

export async function listClasses(signal?: AbortSignal): Promise<ClassSummary[]> {
  const body = object(await get('/api/classes', signal), 'classes')
  return list(body, 'classes').map(decodeClass)
}

export async function getClass(classId: string, signal?: AbortSignal): Promise<ClassSummary> {
  return decodeClass(await get(base(classId), signal))
}


export async function listRevisions(
  classId: string,
  signal?: AbortSignal,
): Promise<RevisionSummary[]> {
  const body = object(await get(`${base(classId)}/revisions`, signal), 'revisions')
  return list(body, 'revisions').map((r) => decodeRevisionSummary(object(r, 'revision')))
}


export async function getRevision(
  classId: string,
  revisionId: string,
  signal?: AbortSignal,
): Promise<Revision> {
  const body = object(
    await get(`${base(classId)}/revisions/${encodeURIComponent(revisionId)}`, signal),
    'revision',
  )
  return {
    ...decodeRevisionSummary(body),
    ...decodeDraftContent(body.content),
    unknownBpm: decodeUnknownBpm(body),
    tssApproximate: optionalFlag(body, 'tss_approximate') ?? false,
    ruleHits: decodeRuleHits(body),
    notesUseCurrentRules: optionalFlag(body, 'notes_use_current_rules') ?? false,
    raw: body.content,
    interpretation: decodeInterpretationItems(isObject(body.provenance) ? body.provenance.interpretation : null),
  }
}

export interface ClassMessage {
  seq: number
  role: 'user' | 'assistant'
  /** The user's request, or the model's note: untrusted text, shown as plain text only. */
  body: string
  jobId: string | null
  baseRevisionId: string | null
  resultRevisionId: string | null
  change: ChangeSummary | null
  createdAt: string
}

export async function listMessages(
  classId: string,
  signal?: AbortSignal,
): Promise<ClassMessage[]> {
  const body = object(await get(`${base(classId)}/messages`, signal), 'messages')
  return list(body, 'messages').map((entry) => {
    const item = object(entry, 'message')
    return {
      seq: count(item, 'seq'),
      role: text(item, 'role') === 'user' ? 'user' : 'assistant',
      body: text(item, 'body'),
      jobId: optionalText(item, 'job_id'),
      baseRevisionId: optionalText(item, 'base_revision_id'),
      resultRevisionId: optionalText(item, 'result_revision_id'),
      change: decodeChange(item.change),
      createdAt: text(item, 'created_at'),
    }
  })
}

/** Makes `revisionId` the current revision. `contentHash` is the hash of the content shown. */
export async function pickCurrent(
  classId: string,
  revisionId: string,
  contentHash: string,
): Promise<ClassSummary> {
  return decodeClass(
    await post(`${base(classId)}/current`, { revision_id: revisionId, content_hash: contentHash }),
  )
}

export async function deleteClass(classId: string, confirmUncertain: boolean): Promise<Deleted> {
  const query = confirmUncertain ? '?confirm_uncertain=true' : ''
  return decodeDeleted(await del(`${base(classId)}${query}`))
}

/** Dismiss a pattern note for this class (ADR 0017 section 4); idempotent. */
export async function dismissRule(classId: string, ruleId: string): Promise<void> {
  await request('PUT', `${base(classId)}/dismissed-rules/${encodeURIComponent(ruleId)}`)
}

/** Undo a dismissal ("Show again"); idempotent. */
export async function showRuleAgain(classId: string, ruleId: string): Promise<void> {
  await del(`${base(classId)}/dismissed-rules/${encodeURIComponent(ruleId)}`)
}
