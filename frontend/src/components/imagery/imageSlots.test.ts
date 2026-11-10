import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../../i18n/en'
import da from '../../i18n/da'
import sv from '../../i18n/sv'
import untranslated from '../../i18n/untranslated.json'
import {
  FORMATS,
  PHONE_MEDIA,
  SLOTS,
  filesOf,
  fileUrl,
  largest,
  resolveSlot,
  srcSet,
  type ImageSet,
  type SlotId,
} from './imageSlots'

const publicDir = join(import.meta.dirname, '..', '..', '..', 'public')
const imgDir = join(publicDir, 'img')
const css = readFileSync(join(import.meta.dirname, '..', '..', 'styles', 'imagery.css'), 'utf8')

/** The pixel size a file says it has: JPEG start-of-frame, WebP (VP8, VP8L, VP8X), AVIF `ispe`. */
function sizeOf(path: string): [number, number] {
  const b = readFileSync(path)
  if (path.endsWith('.jpg')) {
    let pos = 2
    while (pos + 9 < b.length) {
      const marker = b[pos + 1] ?? 0
      if (marker >= 0xc0 && marker <= 0xc2) return [b.readUInt16BE(pos + 7), b.readUInt16BE(pos + 5)]
      pos += 2 + b.readUInt16BE(pos + 2)
    }
  } else if (path.endsWith('.webp')) {
    const kind = b.toString('ascii', 12, 16)
    if (kind === 'VP8 ') return [b.readUInt16LE(26) & 0x3fff, b.readUInt16LE(28) & 0x3fff]
    if (kind === 'VP8X') return [b.readUIntLE(24, 3) + 1, b.readUIntLE(27, 3) + 1]
    if (kind === 'VP8L') {
      const bits = b.readUInt32LE(21)
      return [(bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1]
    }
  } else if (path.endsWith('.avif')) {
    const at = b.indexOf('ispe', 0, 'ascii')
    if (at > 0) return [b.readUInt32BE(at + 8), b.readUInt32BE(at + 12)]
  }
  throw new Error(`cannot read the size of ${path}`)
}

const diskPath = (url: string) => join(publicDir, ...url.split('/'))

const everySet = (): { slot: SlotId; role: string; set: ImageSet }[] =>
  (Object.keys(SLOTS) as SlotId[]).flatMap((slot) => {
    const s = SLOTS[slot]
    return [
      ...Object.entries(s.sets).map(([theme, set]) => ({ slot, role: `${theme}`, set })),
      ...Object.entries(s.phone ?? {}).map(([theme, set]) => ({ slot, role: `${theme} phone`, set })),
    ]
  })

describe('the slot manifest', () => {
  it('lists 22 picture sets (hero 4, steps 6, sign-in panel 2, the empty states 2 shared by two slots, games 8)', () => {
    expect(new Set(everySet().map((e) => e.set.name)).size).toBe(22)
    expect(everySet().length).toBe(24)
    expect(SLOTS.emptyHome.sets).toEqual(SLOTS.emptyClasses.sets)
  })

  it.each(everySet().map((e) => [`${e.slot} ${e.role}`, e.set] as const))(
    '%s: every file exists, in all three formats, at the size the manifest says',
    (_name, set) => {
      expect(set.sizes.length).toBeGreaterThanOrEqual(2)
      for (const [width, height] of set.sizes) {
        for (const { ext } of FORMATS) {
          const path = diskPath(fileUrl(set, width, ext))
          expect(statSync(path).size, path).toBeGreaterThan(0)
          expect(sizeOf(path), path).toEqual([width, height])
        }
      }
    },
  )

  it('sizes run from the smallest to the largest and keep one aspect ratio', () => {
    for (const { set } of everySet()) {
      const widths = set.sizes.map(([w]) => w)
      expect(widths).toEqual([...widths].sort((a, b) => a - b))
      const ratios = set.sizes.map(([w, h]) => Math.round((w / h) * 100) / 100)
      expect(new Set(ratios).size, set.name).toBe(1)
    }
  })

  it('every file in public/img belongs to a set (or is the link preview)', () => {
    const listed = new Set(everySet().flatMap(({ set }) => filesOf(set).map((url) => url.split('/').pop())))
    listed.add('og-share.jpg')
    const onDisk = readdirSync(imgDir)
    expect(onDisk.filter((f) => !listed.has(f))).toEqual([])
    expect([...listed].filter((f) => !onDisk.includes(f as string))).toEqual([])
  })

  it('a phone crop is a smaller picture than the wide one and weighs 150 KB or less', () => {
    for (const slot of Object.values(SLOTS)) {
      for (const [theme, phone] of Object.entries(slot.phone ?? {})) {
        const wide = slot.sets[theme as 'dark' | 'light']
        expect(wide, theme).toBeDefined()
        expect(largest(phone)[0]).toBeLessThan(largest(wide as ImageSet)[0])
        // what a phone downloads: the best format at the largest phone width
        const best = statSync(diskPath(fileUrl(phone, largest(phone)[0], 'avif'))).size
        expect(best).toBeLessThanOrEqual(150 * 1024)
      }
    }
  })

  it('the light and the dark picture of a slot have the same sizes, so the layout does not move with the theme', () => {
    for (const [id, slot] of Object.entries(SLOTS)) {
      const { light, dark } = slot.sets
      if (light && dark) expect(dark.sizes, id).toEqual(light.sizes)
    }
  })

  it('a sign-in panel picture weighs 40 KB or less in AVIF at every width', () => {
    for (const set of Object.values(SLOTS.authPanel.sets)) {
      for (const [width] of set.sizes) {
        expect(statSync(diskPath(fileUrl(set, width, 'avif'))).size, `${set.name} ${String(width)}`).toBeLessThanOrEqual(40 * 1024)
      }
    }
  })

  it('a card or empty-state picture weighs 30 KB or less in AVIF and 60 KB or less in JPEG at every width', () => {
    for (const id of ['stepDescribe', 'stepApprove', 'stepSave', 'emptyHome', 'emptyClasses', 'gameRacetrack', 'gamePowerPull', 'gameFastLane', 'gameOverflow'] as const) {
      for (const set of Object.values(SLOTS[id].sets)) {
        for (const [width] of set.sizes) {
          expect(statSync(diskPath(fileUrl(set, width, 'avif'))).size, `${set.name} ${String(width)} avif`).toBeLessThanOrEqual(30 * 1024)
          expect(statSync(diskPath(fileUrl(set, width, 'jpg'))).size, `${set.name} ${String(width)} jpg`).toBeLessThanOrEqual(60 * 1024)
        }
      }
    }
  })

  it('the empty-state pictures are 4:3, the box imagery.css gives them', () => {
    for (const set of Object.values(SLOTS.emptyHome.sets)) {
      const [w, h] = largest(set)
      expect(w * 3).toBe(h * 4)
    }
    expect(css).toMatch(/\.slot--empty \{[^}]*aspect-ratio: 4 \/ 3;/)
  })

  it('the link preview is 1200 x 630 and weighs 150 KB or less', () => {
    const path = join(imgDir, 'og-share.jpg')
    expect(sizeOf(path)).toEqual([1200, 630])
    expect(statSync(path).size).toBeLessThanOrEqual(150 * 1024)
  })

  it('the phone breakpoint is the one imagery.css uses', () => {
    const queries = [...css.matchAll(/@media \((max-width: [^)]+)\)/g)].map((m) => `(${m[1] ?? ''})`)
    expect(queries).toContain(PHONE_MEDIA)
  })
})

describe('alt text', () => {
  const keys = Object.values(SLOTS).flatMap((s) => (s.alt === null ? [] : [s.alt]))

  it('a slot with a description has its key in every language, short and not empty', () => {
    expect(keys).toEqual([
      'landing.img.describe',
      'landing.img.approve',
      'landing.img.save',
      'class.game.img.racetrack',
      'class.game.img.power_pull',
      'class.game.img.fast_lane',
      'class.game.img.overflow',
    ])
    for (const catalogue of [en, sv, da] as Record<string, unknown>[]) {
      // the game keys are English only until MA6 (ADR 0023 section 8; untranslated.json)
      for (const key of keys.filter((k) => catalogue === en || !untranslated.includes(k))) {
        const text = catalogue[key]
        expect(typeof text, key).toBe('string')
        expect((text as string).length, key).toBeGreaterThan(5)
        expect((text as string).length, key).toBeLessThanOrEqual(80)
      }
    }
  })

  it('the hero, the sign-in panel and the empty states are decorative', () => {
    for (const id of ['hero', 'authPanel', 'emptyHome', 'emptyClasses'] as const) expect(SLOTS[id].alt, id).toBeNull()
  })
})

describe('resolveSlot', () => {
  it('gives a theme its own picture', () => {
    expect(resolveSlot('hero', 'dark')?.set.name).toBe('hero-dark')
    expect(resolveSlot('hero', 'light')?.set.name).toBe('hero-light')
    expect(resolveSlot('hero', 'dark')?.dimmed).toBe(false)
    expect(resolveSlot('hero', 'dark')?.phone?.name).toBe('hero-dark-phone')
  })

  it('gives the steps, the sign-in panel and the empty states a picture of their own in each theme, never dimmed', () => {
    const names = {
      stepDescribe: 'step-describe',
      stepApprove: 'step-approve',
      stepSave: 'step-save',
      authPanel: 'auth-panel',
      emptyHome: 'empty-home',
      emptyClasses: 'empty-home',
    } as const
    for (const [id, name] of Object.entries(names) as [SlotId, string][]) {
      expect(resolveSlot(id, 'light'), id).toMatchObject({ dimmed: false })
      expect(resolveSlot(id, 'dark'), id).toMatchObject({ dimmed: false })
      expect(resolveSlot(id, 'light')?.set.name, id).toBe(`${name}-light`)
      expect(resolveSlot(id, 'dark')?.set.name, id).toBe(`${name}-dark`)
    }
  })

  it('shows a light-only slot under the dim on dark (`dim`), undimmed on light', () => {
    const saved = SLOTS.stepSave
    SLOTS.stepSave = { ...saved, sets: { light: saved.sets.light } }
    try {
      expect(resolveSlot('stepSave', 'light')).toMatchObject({ dimmed: false })
      expect(resolveSlot('stepSave', 'dark')).toMatchObject({ dimmed: true })
      expect(resolveSlot('stepSave', 'dark')?.set.name).toBe('step-save-light')
    } finally {
      SLOTS.stepSave = saved
    }
  })

  it('shows nothing for a slot without files, or with `hide` and no picture of its own', () => {
    const empty = SLOTS.stepSave
    SLOTS.stepSave = { ...empty, sets: {} }
    try {
      expect(resolveSlot('stepSave', 'light')).toBeNull()
      expect(resolveSlot('stepSave', 'dark')).toBeNull()
    } finally {
      SLOTS.stepSave = empty
    }
    const saved = SLOTS.stepDescribe
    SLOTS.stepDescribe = { ...saved, sets: { light: saved.sets.light }, onDark: 'hide' }
    try {
      expect(resolveSlot('stepDescribe', 'dark')).toBeNull()
    } finally {
      SLOTS.stepDescribe = saved
    }
  })

  it('builds the urls and the srcset from the set', () => {
    const set = { name: 'x', sizes: [[10, 5], [20, 10]] as const }
    expect(fileUrl(set, 10, 'avif')).toBe('/img/x-10.avif')
    expect(srcSet(set, 'webp')).toBe('/img/x-10.webp 10w, /img/x-20.webp 20w')
    expect(largest(set)).toEqual([20, 10])
    expect(filesOf(set)).toHaveLength(6)
  })
})
