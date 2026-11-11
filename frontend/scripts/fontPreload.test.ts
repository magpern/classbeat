import { describe, expect, it } from 'vitest'
import { fontPreloadTags } from './fontPreload'

describe('fontPreloadTags', () => {
  it('preloads only the Latin Inter and Archivo files, as crossorigin fonts', () => {
    const tags = fontPreloadTags([
      'assets/index-abc.js',
      'assets/inter-latin-wght-normal-Dx4kXJAl.woff2',
      'assets/inter-latin-ext-wght-normal-DO1Apj_S.woff2',
      'assets/inter-greek-wght-normal-CkhJZR-_.woff2',
      'assets/archivo-latin-wdth-normal-DY7AcnAa.woff2',
      'assets/archivo-latin-ext-wdth-normal-7khWdh9v.woff2',
    ])
    expect(tags.map((t) => t.attrs?.href)).toEqual([
      '/assets/archivo-latin-wdth-normal-DY7AcnAa.woff2',
      '/assets/inter-latin-wght-normal-Dx4kXJAl.woff2',
    ])
    for (const tag of tags) {
      expect(tag.attrs).toMatchObject({ rel: 'preload', as: 'font', type: 'font/woff2', crossorigin: '' })
    }
  })

  it('adds nothing when the bundle has no such font', () => {
    expect(fontPreloadTags(['assets/index-abc.js'])).toEqual([])
  })
})
