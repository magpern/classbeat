import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EXAMPLE_PROFILE, EXAMPLE_SONGS } from '../components/landing/exampleClass'
import { setWidth } from '../test/layout'
import { fakeApi } from '../test/fakeApi'
import { renderAt } from '../test/render'

describe('Landing', () => {
  it('has the headline, one call to action and the sign-in link only in the header', async () => {
    setWidth(390)
    fakeApi({})
    renderAt('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Your class, timed to the music.' })).toBeInTheDocument()
    const cta = screen.getByRole('link', { name: 'Join the beta' })
    expect(cta).toHaveAttribute('href', '/en/signup')
    // The page body has the one action; Sign in and Sign up are the header's.
    const body = screen.getByRole('main')
    expect(within(body).getAllByRole('link').filter((l) => l.getAttribute('href') === '/en/signin')).toHaveLength(0)
    expect(screen.getAllByRole('link', { name: 'Sign in' })).toHaveLength(1)
  })

  it('shows an example class on the stage with its numbered song list', async () => {
    fakeApi({})
    renderAt('/')
    expect(await screen.findByRole('img', { name: /Class profile, 30 min, 20 intervals/ })).toBeInTheDocument()
    const songs = within(screen.getByRole('list', { name: 'Songs of the example class' })).getAllByRole('listitem')
    expect(songs).toHaveLength(EXAMPLE_SONGS.length)
    expect(songs[0]).toHaveTextContent('Sample Song One')
    expect(songs[0]).toHaveTextContent('96 BPM')
  })

  it('names only the sample songs\' own data for the e2e English-text check, never a translated string', async () => {
    fakeApi({})
    renderAt('/')
    const list = await screen.findByRole('list', { name: 'Songs of the example class' })
    const fixture = (list.getAttribute('data-fixture-text') ?? '').split('|')
    for (const song of EXAMPLE_SONGS) expect(fixture).toEqual(expect.arrayContaining([song.title, song.artist, String(song.bpm), song.length]))
    expect(fixture).toHaveLength(EXAMPLE_SONGS.length * 4)
    expect(fixture.join('|')).not.toMatch(/Warm-up|BPM|Songs of the example class/)
  })

  it('shows the three steps', async () => {
    fakeApi({})
    renderAt('/')
    const steps = within(await screen.findByRole('region', { name: 'How it works' })).getAllByRole('listitem')
    expect(steps.map((s) => within(s).getByRole('heading').textContent)).toEqual([
      'Describe',
      'Refine',
      'Approve and save',
    ])
  })

  it('uses only invented songs and artists', () => {
    for (const song of EXAMPLE_SONGS) {
      expect(song.title).toMatch(/^Sample Song /)
      expect(song.artist).toMatch(/^Example Artist /)
    }
    expect(EXAMPLE_PROFILE.reduce((sum, [, sec]) => sum + sec, 0)).toBe(1800)
  })
})

describe('focus pages for visitors', () => {
  it.each([
    ['/signin', 'Sign in'],
    ['/signup', 'Create an account'],
    ['/reset', 'Reset your password'],
  ])('%s is a centred card under the plain header', async (path, title) => {
    setWidth(1440)
    fakeApi({})
    renderAt(path)
    const heading = await screen.findByRole('heading', { level: 1, name: title })
    expect(heading.closest('section')).toHaveClass('max-w-md', 'mx-auto')
    expect(screen.getByRole('navigation', { name: 'Account' })).toHaveTextContent('Sign up')
  })
})
