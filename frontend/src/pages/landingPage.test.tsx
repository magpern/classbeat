import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EXAMPLE_PROFILE, EXAMPLE_SONGS } from '../components/landing/exampleClass'
import { setWidth } from '../test/layout'
import { fakeApi } from '../test/fakeApi'
import { renderAt } from '../test/render'

describe('Landing', () => {
  it('has the headline, the invite sign-up call to action and a sign-in link', async () => {
    setWidth(390)
    fakeApi({})
    renderAt('/')
    expect(await screen.findByRole('heading', { level: 1, name: /classbeat picks the songs/ })).toBeInTheDocument()
    const cta = screen.getByRole('link', { name: 'Sign up with an invite code' })
    expect(cta).toHaveAttribute('href', '/signup')
    expect(screen.getByRole('link', { name: 'I already have an account' })).toHaveAttribute('href', '/signin')
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

  it('shows the three steps', async () => {
    fakeApi({})
    renderAt('/')
    const steps = within(await screen.findByRole('region', { name: 'How it works' })).getAllByRole('listitem')
    expect(steps.map((s) => within(s).getByRole('heading').textContent)).toEqual([
      'Describe',
      'Approve songs',
      'Save to MOWL',
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
