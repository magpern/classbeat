import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { decodeJob } from '../../api/jobs'
import { RouterProvider } from '../../router/Router'
import { CLASS_ID, jobBody } from '../../test/jobFixtures'
import { OutputsEnded } from './OutputsEnded'
import { outputLimitReached } from './studioAction'

function ended(category: string) {
  const job = decodeJob(jobBody('failed', { kind: 'produce_outputs', error_category: category }))
  return render(
    <RouterProvider>
      <OutputsEnded job={job} />
    </RouterProvider>,
  )
}

describe('a failed produce_outputs job', () => {
  beforeEach(() => { window.sessionStorage.clear() })

  it('words the output limit, remembers it, and offers only the way back to the class', () => {
    ended('output_limit')
    const card = screen.getByTestId('outputs-ended')
    expect(card).toHaveTextContent('Output limit reached')
    expect(card).toHaveTextContent('24 attempts per class and per day')
    expect(card).not.toHaveTextContent('Try again later')
    expect(screen.getByRole('link', { name: 'Back to the class' }).getAttribute('href')).toMatch(new RegExp(`/classes/${CLASS_ID}$`))
    expect(screen.queryByRole('button')).toBeNull()
    expect(outputLimitReached(CLASS_ID)).toBe(true)
  })

  it.each([
    ['writes_off', 'writes switched off'],
    ['connection_changed', 'connection changed'],
    ['render_not_reproducible', 'did not render the same'],
  ])('words %s', (category, words) => {
    ended(category)
    expect(screen.getByTestId('outputs-ended')).toHaveTextContent(words)
    expect(screen.getByTestId('outputs-ended')).not.toHaveTextContent('did not finish')
    expect(outputLimitReached(CLASS_ID)).toBe(false)
  })
})
