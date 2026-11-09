import type { CatalogueKey } from '../../i18n/en'

/**
 * The picture slots (issue 854): which photo goes where, in which sizes, with which alt text.
 * Pure data and rules; `SlotPicture` draws them. A slot with no file set is simply not drawn, so a
 * later PR adds a picture by dropping its files into `public/img/` and filling in the set here
 * (how-to: docs/developer-commands.md, "Images").
 */

export type ImageTheme = 'dark' | 'light'
export type SlotId =
  | 'hero'
  | 'stepDescribe'
  | 'stepApprove'
  | 'stepSave'
  | 'authPanel'
  | 'emptyHome'
  | 'emptyClasses'

/** `[width, height]` in pixels of one rendition. */
export type Size = readonly [width: number, height: number]

/** Files `/img/<name>-<width>.avif|webp|jpg`, one per size, smallest first. */
export interface ImageSet {
  name: string
  sizes: readonly Size[]
}

export interface Slot {
  /** The picture per theme. A theme without a set follows `onDark`. */
  sets: Partial<Record<ImageTheme, ImageSet>>
  /** A narrower crop for a phone (`PHONE_MEDIA`), per theme; without one the phone gets `sets`. */
  phone?: Partial<Record<ImageTheme, ImageSet>>
  /** Dark theme with only a light picture: `dim` shows it under the dark dim, `hide` shows nothing. */
  onDark: 'dim' | 'hide'
  /** The alt text, or null when the picture is decorative (empty alt, hidden from assistive tech). */
  alt: CatalogueKey | null
}

/** Where a phone stops getting the wide picture; imagery.css uses the same width (a test checks). */
export const PHONE_MEDIA = '(max-width: 40rem)'

export const IMG_DIR = '/img'
/** Best format first: the browser takes the first `<source>` it can decode. */
export const FORMATS = [
  { ext: 'avif', type: 'image/avif' },
  { ext: 'webp', type: 'image/webp' },
  { ext: 'jpg', type: 'image/jpeg' },
] as const

const set = (name: string, ...sizes: Size[]): ImageSet => ({ name, sizes })

const EMPTY_STATE = set('empty-home-light', [480, 360], [768, 576])

export const SLOTS: Record<SlotId, Slot> = {
  hero: {
    sets: { dark: set('hero-dark', [960, 540], [1600, 900]), light: set('hero-light', [960, 540], [1600, 900]) },
    phone: {
      dark: set('hero-dark-phone', [480, 600], [752, 940]),
      light: set('hero-light-phone', [480, 600], [752, 940]),
    },
    onDark: 'hide',
    alt: null,
  },
  stepDescribe: {
    sets: { light: set('step-describe-light', [480, 320], [768, 512]) },
    onDark: 'dim',
    alt: 'landing.img.describe',
  },
  stepApprove: {
    sets: { light: set('step-approve-light', [480, 320], [768, 512]) },
    onDark: 'dim',
    alt: 'landing.img.approve',
  },
  stepSave: {
    sets: { light: set('step-save-light', [480, 320], [768, 512]) },
    onDark: 'dim',
    alt: 'landing.img.save',
  },
  authPanel: {
    sets: { light: set('auth-panel-light', [480, 600], [960, 1200]) },
    onDark: 'dim',
    alt: null,
  },
  // The empty Home and the empty My classes share one calm picture (two slots, one file set).
  emptyHome: { sets: { light: EMPTY_STATE }, onDark: 'dim', alt: null },
  emptyClasses: { sets: { light: EMPTY_STATE }, onDark: 'dim', alt: null },
}

export interface ResolvedSlot {
  set: ImageSet
  phone: ImageSet | undefined
  /** The light picture is shown on the dark page, under the dim. */
  dimmed: boolean
  alt: CatalogueKey | null
}

/** What a slot shows in `theme`, or null when it shows nothing. */
export function resolveSlot(id: SlotId, theme: ImageTheme): ResolvedSlot | null {
  const slot = SLOTS[id]
  const own = slot.sets[theme]
  if (own) return { set: own, phone: slot.phone?.[theme], dimmed: false, alt: slot.alt }
  const light = slot.sets.light
  if (theme === 'dark' && light && slot.onDark === 'dim') {
    return { set: light, phone: slot.phone?.light, dimmed: true, alt: slot.alt }
  }
  return null
}

export const fileUrl = (set: ImageSet, width: number, ext: string): string => `${IMG_DIR}/${set.name}-${String(width)}.${ext}`

export function srcSet(set: ImageSet, ext: string): string {
  return set.sizes.map(([width]) => `${fileUrl(set, width, ext)} ${String(width)}w`).join(', ')
}

export function largest(set: ImageSet): Size {
  const last = set.sizes[set.sizes.length - 1]
  if (!last) throw new Error(`image set ${set.name} has no sizes`)
  return last
}

/** Every file a set is made of, as paths below `public/` (for the tests and the how-to). */
export function filesOf(set: ImageSet): string[] {
  return set.sizes.flatMap(([width]) => FORMATS.map(({ ext }) => fileUrl(set, width, ext)))
}
