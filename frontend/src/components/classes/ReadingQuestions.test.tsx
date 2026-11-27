import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { decodeReadingExtras, type ReadingQuestion } from '../../api/readingExtras'
import type { Confirmation } from '../../hooks/useHandoff'
import { I18nProvider } from '../../i18n/I18n'
import { fakeApi } from '../../test/fakeApi'
import { ReadingQuestions } from './ReadingQuestions'

const READING = 'a1111111-1111-4111-8111-111111111111'
const PLAYLIST = '0123456789abcdefABCDEF'

const LENGTH = { topic: 'length', text: 'How long should it be?', options: ['30 minutes', '45 minutes', '60 minutes'], assumed_index: 1 }
const MUSIC = { topic: 'music', text: 'What about the music?', options: ['80s hits', 'Rock', 'No music'], no_music_index: 2 }
const CLOSED = { topic: 'music', code: 'which_music', options: ['my_playlists', 'no_music'], no_music_index: 1 }

function questionsOf(...raw: object[]): ReadingQuestion[] {
  return decodeReadingExtras({ questions: raw }).questions
}

function show(questions: ReadingQuestion[], more: Partial<Confirmation> = {}, composer: boolean | 'fails' = true) {
  // The composer is available to the user (`GET /api/connections`' `composer_flow`) unless the test says the classic flow.
  const api = fakeApi({
    'GET /api/connections': composer === 'fails' ? () => new Response('', { status: 500 }) : { connections: [], composer_flow: composer },
    'GET /api/music/mowl-playlists': { playlists: [{ spotify_playlist_id: PLAYLIST, name: 'Tuesday ride' }] },
  })
  const confirm: Confirmation = {
    interpretJobId: READING,
    onDesign: vi.fn(),
    onReread: vi.fn(() => Promise.resolve(null)),
    rereadFailed: false,
    ...more,
  }
  render(
    <I18nProvider initial="en">
      <ReadingQuestions questions={questions} confirm={confirm} />
    </I18nProvider>,
  )
  return Object.assign(confirm, { api })
}

const design = () => screen.getByRole('button', { name: 'Design this class' })
const update = () => screen.getByRole('button', { name: 'Update the reading' })

describe('the confirm screen', () => {
  it('shows each question with its assumed answer pre-selected and marked as assumed', () => {
    show(questionsOf(LENGTH))
    const group = screen.getByRole('group', { name: 'How long should it be?' })
    expect(within(group).getByRole('radio', { name: /45 minutes/ })).toBeChecked()
    expect(within(group).getByRole('radio', { name: /30 minutes/ })).not.toBeChecked()
    expect(within(group).getByText('(assumed)')).toBeInTheDocument()
  })

  it('confirms with one click when nothing changed, and sends no re-read', async () => {
    const confirm = show(questionsOf(LENGTH))
    expect(update()).toBeDisabled()
    await userEvent.click(design())
    expect(confirm.onDesign).toHaveBeenCalledTimes(1)
    expect(confirm.onReread).not.toHaveBeenCalled()
  })

  it('shows the confirm screen for a reading that asked nothing', async () => {
    const confirm = show([])
    expect(screen.getByLabelText('Anything to correct?')).toBeInTheDocument()
    await userEvent.click(design())
    expect(confirm.onDesign).toHaveBeenCalledTimes(1)
  })

  it('re-reads, never confirms, when an answer was changed', async () => {
    const confirm = show(questionsOf(LENGTH))
    await userEvent.click(screen.getByRole('radio', { name: /60 minutes/ }))
    expect(design()).toBeDisabled() // the unsaved change is not dropped silently
    await userEvent.click(update())
    expect(confirm.onReread).toHaveBeenCalledWith({ interpretJobId: READING, answers: [{ questionIndex: 0, optionIndex: 2 }] })
    expect(confirm.onDesign).not.toHaveBeenCalled()
  })

  it('re-reads with the words typed under "Anything to correct?"', async () => {
    const confirm = show(questionsOf(LENGTH))
    await userEvent.type(screen.getByLabelText('Anything to correct?'), 'Short climbs')
    await userEvent.click(update())
    expect(confirm.onReread).toHaveBeenCalledWith({ interpretJobId: READING, answers: [], note: 'Short climbs' })
    expect(confirm.onDesign).not.toHaveBeenCalled()
  })

  it('has no pre-selection for a music question with no hint, and both buttons wait for its answer', async () => {
    const confirm = show(questionsOf(MUSIC))
    expect(screen.getAllByRole('radio').every((r) => !(r as HTMLInputElement).checked)).toBe(true)
    expect(screen.queryByText('(assumed)')).toBeNull()
    expect(screen.getByText(/no songs are picked until you do/)).toBeInTheDocument()
    expect(design()).toBeDisabled()
    expect(update()).toBeDisabled()
    await userEvent.click(screen.getByRole('radio', { name: 'Rock' }))
    expect(update()).toBeEnabled()
    expect(design()).toBeDisabled() // the answer is a change: the request is read again first
    await userEvent.click(update())
    expect(confirm.onReread).toHaveBeenCalledWith({ interpretJobId: READING, answers: [{ questionIndex: 0, optionIndex: 1 }] })
  })

  it('takes a typed style for the required music question', async () => {
    const confirm = show(questionsOf(MUSIC))
    await userEvent.type(screen.getByLabelText('Or type your own answer'), '80s rock')
    await userEvent.click(update())
    expect(confirm.onReread).toHaveBeenCalledWith({ interpretJobId: READING, answers: [{ questionIndex: 0, text: '80s rock' }] })
  })

  it('uses the music question\'s own "No music" option and offers no second one', async () => {
    const confirm = show(questionsOf(MUSIC))
    expect(screen.queryByRole('checkbox', { name: 'No music' })).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: 'No music' }))
    await userEvent.click(update())
    expect(confirm.onReread).toHaveBeenCalledWith({ interpretJobId: READING, answers: [{ questionIndex: 0, optionIndex: 2 }] })
  })

  it('offers "No music" on a reading with no music question and sends the closed field', async () => {
    const confirm = show(questionsOf(LENGTH))
    await userEvent.click(screen.getByRole('checkbox', { name: 'No music' }))
    expect(design()).toBeDisabled()
    await userEvent.click(update())
    expect(confirm.onReread).toHaveBeenCalledWith({ interpretJobId: READING, answers: [], noMusic: true })
  })

  it('sends the picked playlists with the closed "Use one of my playlists" answer', async () => {
    const confirm = show(questionsOf(CLOSED))
    await userEvent.click(await screen.findByRole('radio', { name: 'Use one of my playlists' }))
    expect(update()).toBeDisabled() // no playlist yet: it would loop back to the same question
    await userEvent.click(await screen.findByRole('button', { name: /Add music/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Tuesday ride' }))
    await userEvent.click(update())
    expect(confirm.onReread).toHaveBeenCalledWith({
      interpretJobId: READING,
      answers: [{ questionIndex: 0, optionIndex: 0 }],
      musicSources: [PLAYLIST],
    })
  })

  it('offers "Use one of my playlists" and its picker in the composer flow', async () => {
    show(questionsOf(CLOSED))
    await userEvent.click(await screen.findByRole('radio', { name: 'Use one of my playlists' }))
    expect(await screen.findByTestId('reading-playlists')).toBeInTheDocument()
  })

  it('offers no playlist option or picker in the classic flow, and keeps "No music" and a typed answer', async () => {
    const { api } = show(questionsOf(CLOSED), {}, false)
    await waitFor(() => {
      expect(api.to('GET', '/api/connections').length).toBeGreaterThan(0)
    })
    await new Promise((resolve) => setTimeout(resolve, 30)) // the answer is in
    expect(screen.queryByRole('radio', { name: 'Use one of my playlists' })).toBeNull()
    expect(screen.queryByTestId('reading-playlists')).toBeNull()
    expect(screen.getByRole('radio', { name: 'No music' })).toBeInTheDocument()
    expect(screen.getByLabelText('Or type your own answer')).toBeInTheDocument()
  })

  it('offers no playlist option when the connections read fails, and keeps "No music"', async () => {
    const { api } = show(questionsOf(CLOSED), {}, 'fails')
    await waitFor(() => {
      expect(api.to('GET', '/api/connections').length).toBeGreaterThan(0)
    })
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(screen.queryByRole('radio', { name: 'Use one of my playlists' })).toBeNull()
    expect(screen.getByRole('radio', { name: 'No music' })).toBeInTheDocument()
  })

  it('says why a re-read was refused and keeps the answers', async () => {
    const refused = new ApiError(409, 'chat_full', null, {})
    const confirm = show(questionsOf(LENGTH), { onReread: vi.fn(() => Promise.resolve(refused)) })
    await userEvent.click(screen.getByRole('radio', { name: /60 minutes/ }))
    await userEvent.click(update())
    expect(await screen.findByText(/reached its limit of requests/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /60 minutes/ })).toBeChecked()
    await waitFor(() => {
      expect(update()).toBeEnabled()
    })
    expect(confirm.onDesign).not.toHaveBeenCalled()
  })

  it('says when the last re-read ended with no new reading', () => {
    show(questionsOf(LENGTH), { rereadFailed: true })
    expect(screen.getByText(/could not be updated/)).toBeInTheDocument()
  })
})
