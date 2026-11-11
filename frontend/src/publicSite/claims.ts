import type { CatalogueKey } from '../i18n/en'

// The product claims of the public pages as data (PP4, #893). Each claim names the shipped route,
// flag or plan limit it rests on (`evidence`, not rendered) and its availability class, which the
// page renders as a visible note. Pure: no I/O, so a test can check every claim and the pages' wording.

/** What a visitor needs before the feature works. */
export type Availability = 'available' | 'connection' | 'gated'
export type Connection = 'mowl' | 'spotify' | 'swedebeat'

export interface Claim {
  id: string
  title: CatalogueKey
  text: CatalogueKey
  availability: Availability
  /** The note shown with the claim: what it needs, in words. */
  note: CatalogueKey
  /** The connections the feature needs. */
  needs: readonly Connection[]
  /** The shipped route, flag or plan limit that makes the claim true. */
  evidence: string
  /** True for a claim that classbeat picks songs: its note must name Spotify, the Client ID and the plan. */
  songPicking?: true
}

export const CLASS_LABEL: Readonly<Record<Availability, CatalogueKey>> = {
  available: 'claims.class.available',
  connection: 'claims.class.connection',
  gated: 'claims.class.gated',
}

export interface ClaimGroup {
  id: string
  title: CatalogueKey
  claims: readonly Claim[]
}

const MOWL = { availability: 'connection', note: 'claims.note.mowl', needs: ['mowl'] } as const

/** The Features page, grouped by outcome. */
export const FEATURE_GROUPS: readonly ClaimGroup[] = [
  {
    id: 'request',
    title: 'feat.group.request',
    claims: [
      {
        id: 'describe',
        title: 'feat.describe.title',
        text: 'feat.describe.text',
        ...MOWL,
        evidence: 'Start screen at /classes/new; interpret_request job (connection: mowl)',
      },
      {
        id: 'assumptions',
        title: 'feat.assumptions.title',
        text: 'feat.assumptions.text',
        ...MOWL,
        evidence: 'Request panel of the workspace: As you asked, Assumed, Not as asked (interp.*, ADR 0029 section 4)',
      },
      {
        id: 'advanced',
        title: 'feat.advanced.title',
        text: 'feat.advanced.text',
        ...MOWL,
        evidence: 'Form at /classes/new/advanced; design job (connection: mowl)',
      },
    ],
  },
  {
    id: 'music',
    title: 'feat.group.music',
    claims: [
      {
        id: 'playlist',
        title: 'feat.playlist.title',
        text: 'feat.playlist.text',
        ...MOWL,
        evidence: 'Music source mowl_spotify_playlist, a playlist in the MOWL music (musicSources.ts)',
      },
      {
        id: 'songs',
        title: 'feat.songs.title',
        text: 'feat.songs.text',
        availability: 'gated',
        note: 'claims.note.songPicking',
        needs: ['mowl', 'spotify'],
        songPicking: true,
        evidence:
          'design_with_songs job (connections: mowl, spotify); CLASSBEAT_SONG_PICKING (default off), flag song_picking and plan feature song_picking; ADR 0008',
      },
      {
        id: 'swedebeat',
        title: 'feat.swedebeat.title',
        text: 'feat.swedebeat.text',
        availability: 'gated',
        note: 'claims.note.swedebeat',
        needs: ['mowl', 'swedebeat'],
        evidence: 'Music source mowl_swedebeat_mix; CLASSBEAT_SWEDEBEAT_ENABLED (default off) and flag swedebeat_enabled; ADR 0015, 0016',
      },
      {
        id: 'noMusic',
        title: 'feat.noMusic.title',
        text: 'feat.noMusic.text',
        availability: 'gated',
        note: 'claims.note.switch',
        needs: ['mowl'],
        evidence: 'Music source none; CLASSBEAT_SOUNDLESS_ENABLED (default off) and a model route that covers it (flags/effective.py)',
      },
    ],
  },
  {
    id: 'shape',
    title: 'feat.group.shape',
    claims: [
      {
        id: 'profile',
        title: 'feat.profile.title',
        text: 'feat.profile.text',
        ...MOWL,
        evidence: 'Class page: ProfileChart, segment cards, class.card.tss',
      },
      {
        id: 'chat',
        title: 'feat.chat.title',
        text: 'feat.chat.text',
        ...MOWL,
        evidence: 'Chat of the workspace; revise job (connection: mowl); immutable revisions, ADR 0007',
      },
      {
        id: 'versions',
        title: 'feat.versions.title',
        text: 'feat.versions.text',
        ...MOWL,
        evidence: 'History and compare (class.compare.*), Use this version (class.useThisVersion), Duplicate (class.duplicate.*)',
      },
      {
        id: 'games',
        title: 'feat.games.title',
        text: 'feat.games.text',
        availability: 'gated',
        note: 'claims.note.games',
        needs: ['mowl'],
        evidence: 'plans.games_allowed (free: no); four games in games_catalog.py, cycling only; class.game.licence; ADR 0029 G1, G2',
      },
      {
        id: 'running',
        title: 'feat.running.title',
        text: 'feat.running.text',
        availability: 'gated',
        note: 'claims.note.switch',
        needs: ['mowl'],
        evidence: 'CLASSBEAT_RUNNING_ENABLED (default off) and flag running_enabled; a playlist from the MOWL music (interp.reason.needs_mowl_playlist); no games on running (ADR 0018, 0029)',
      },
    ],
  },
  {
    id: 'control',
    title: 'feat.group.control',
    claims: [
      {
        id: 'save',
        title: 'feat.save.title',
        text: 'feat.save.text',
        ...MOWL,
        evidence: 'save and update jobs (connection: mowl); class.saved says "private workout"; ADR 0007',
      },
      {
        id: 'limits',
        title: 'feat.limits.title',
        text: 'feat.limits.text',
        availability: 'gated',
        note: 'claims.note.plan',
        needs: [],
        evidence: 'plans.classes_per_month, plans.revisions_per_class, plans.max_class_minutes; Plan and usage tab; ADR 0011',
      },
      {
        id: 'data',
        title: 'feat.data.title',
        text: 'feat.data.text',
        availability: 'available',
        note: 'claims.note.available',
        needs: [],
        evidence: 'Account page: export and delete (ADR 0014; help.data.export, help.data.delete)',
      },
      {
        id: 'languages',
        title: 'feat.languages.title',
        text: 'feat.languages.text',
        availability: 'available',
        note: 'claims.note.available',
        needs: [],
        evidence: 'English, Swedish and Danish catalogues (ADR 0023); generated classes stay English (ADR 0023 section 7)',
      },
    ],
  },
]
