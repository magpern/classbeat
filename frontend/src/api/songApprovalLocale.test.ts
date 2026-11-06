import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { contentHash } from '../lib/canonical'
import { DEFAULT_LOCALE, setActiveLocale } from '../i18n/locales'
import { loadCatalogue, resetCatalogues, t } from '../i18n/t'

// A made-up second locale, so the active locale can be switched to it (an unlisted code stays English).
vi.mock('../i18n/locales.json', () => ({ default: ['en', 'xx'] }))

const post = vi.fn((...args: [string, unknown]) => Promise.reject(new Error(`stopped ${args[0]}`)))
vi.mock('./client', async (original) => ({
  ...(await original<typeof import('./client')>()),
  post: (path: string, body: unknown) => post(path, body),
}))

const { approveSongs, decodeSongList } = await import('./songs')

const payload = {
  id: 'list-1',
  number: 1,
  status: 'proposed',
  content_hash: 'a'.repeat(64),
  mode: 'standard',
  target_sec: 600,
  total_sec: 590,
  songs: [
    { position: 1, title: 'Tüesday', artist: 'Åsa', role: 'main', bpm: 120, length_sec: 300, new: false },
    { position: 2, title: 'Climb', artist: 'B', role: 'main', bpm: 128, length_sec: 300, new: true },
  ],
}

// ADR 0008 section 5: approving a song list sends the revision id and the content hash of the list,
// nothing else. The text around it (the class page strings) never goes into that hash, so the
// approval of a list is the same in every language.
describe('approving a song list in another language', () => {
  beforeEach(() => {
    post.mockClear()
    resetCatalogues()
  })
  afterEach(() => {
    setActiveLocale(DEFAULT_LOCALE)
  })

  it('sends the same hash body whichever locale the page text is in', async () => {
    const bodies: unknown[] = []
    for (const locale of ['en', 'xx']) {
      await loadCatalogue(locale, {
        xx: () => Promise.resolve({ default: { 'class.save.title': 'Spara till MOWL' } }),
      })
      // The page text changes with the active locale, set again on each pass ...
      setActiveLocale(locale)
      const title = t('class.save.title')
      expect(title).toBe(locale === 'en' ? 'Save to MOWL' : 'Spara till MOWL')
      // ... the list and its hash do not.
      const list = decodeSongList(payload)
      expect(list.contentHash).toBe(payload.content_hash)
      await approveSongs('job-1', list.id, list.contentHash).catch(() => undefined)
      bodies.push(post.mock.calls.at(-1))
    }
    expect(bodies[0]).toEqual(bodies[1])
    expect(bodies[0]).toEqual([
      '/api/jobs/job-1/approve',
      { revision_id: 'list-1', content_hash: 'a'.repeat(64) },
    ])
  })

  it('hashes the list content the same in every locale', async () => {
    const before = await contentHash(payload.songs)
    await loadCatalogue('xx', {
      xx: () => Promise.resolve({ default: { 'class.save.title': 'Spara till MOWL' } }),
    })
    setActiveLocale('xx')
    expect(t('class.save.title')).toBe('Spara till MOWL')
    expect(await contentHash(payload.songs)).toBe(before)
  })
})
