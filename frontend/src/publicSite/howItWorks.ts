import type { CatalogueKey } from '../i18n/en'
import type { Claim } from './claims'

// The four steps and the connections of How it works (PP4, #893), as data for the page and as claims
// for the claim table. The steps are statements about every class, so they need MOWL like the
// features they summarise; the connection rows say what each account is for.

export interface Step {
  id: string
  title: CatalogueKey
  text: CatalogueKey
  evidence: string
}

export const STEPS: readonly Step[] = [
  {
    id: 'describe',
    title: 'how.step.describe.title',
    text: 'how.step.describe.text',
    evidence: 'Start screen (start.prompt.hint: anything left out, classbeat chooses); request panel shows what was assumed',
  },
  {
    id: 'generate',
    title: 'how.step.generate.title',
    text: 'how.step.generate.text',
    evidence: 'design job and class profile; help.design.how.a: designing can take a few minutes and the page can be left',
  },
  {
    id: 'refine',
    title: 'how.step.refine.title',
    text: 'how.step.refine.text',
    evidence: 'Chat of the workspace; every change is a new immutable revision, earlier ones stay (help.design.change.a)',
  },
  {
    id: 'save',
    title: 'how.step.save.title',
    text: 'how.step.save.text',
    evidence: 'Save to MOWL on the class page writes the workout, private (class.saved); start.destination says only after you approve',
  },
]

export interface ConnectionRow extends Claim {
  name: CatalogueKey
  required: boolean
}

/** The connections a visitor will be asked for, with what each is for. */
export const CONNECTIONS: readonly ConnectionRow[] = [
  {
    id: 'mowl',
    name: 'how.connect.mowl.name',
    title: 'how.connect.mowl.name',
    text: 'how.connect.mowl.text',
    required: true,
    availability: 'connection',
    note: 'claims.note.mowl',
    needs: ['mowl'],
    evidence: 'conn.mowl.purpose (needed to save classes); design, revise and save jobs have connection mowl; help.connect.mowl',
  },
  {
    id: 'spotify',
    name: 'how.connect.spotify.name',
    title: 'how.connect.spotify.name',
    text: 'how.connect.spotify.text',
    required: false,
    availability: 'gated',
    note: 'claims.note.songPicking',
    needs: ['spotify'],
    songPicking: true,
    evidence: 'Spotify wizard with the user\'s own Client ID (ADR 0009, BYOK); conn.optional; song picking is gated as in the Features table',
  },
  {
    id: 'swedebeat',
    name: 'how.connect.swedebeat.name',
    title: 'how.connect.swedebeat.name',
    text: 'how.connect.swedebeat.text',
    required: false,
    availability: 'gated',
    note: 'claims.note.swedebeat',
    needs: ['swedebeat'],
    evidence: 'CLASSBEAT_SWEDEBEAT_ENABLED (default off); conn.optional; help.connect.swedebeat; ADR 0015',
  },
]
