import { afterEach, describe, expect, it, vi } from 'vitest'
import { EXAMPLE_TTL_MS, forgetExample, recallExample, rememberExample, returnToWithExample, validExample } from './exampleHandoff'

afterEach(() => {
  window.localStorage.clear()
  vi.restoreAllMocks()
})

describe('validExample', () => {
  it('accepts only the slugs of the examples', () => {
    expect(validExample('hill-challenge')).toBe('hill-challenge')
    for (const bad of [null, '', 'Hill-Challenge', 'hill-challenge ', 'nope', '<script>', 'https://evil.example', '../x', 'constructor']) {
      expect(validExample(bad), String(bad)).toBeNull()
    }
  })
})

describe('returnToWithExample', () => {
  it('carries the validated example and nothing else from the query', () => {
    expect(returnToWithExample('/classes/new', '?example=endurance-ride&from=abc&prompt=hello&return_to=//evil.example')).toBe(
      '/classes/new?example=endurance-ride',
    )
  })
  it('is the path alone for an unknown or missing example', () => {
    expect(returnToWithExample('/classes/new', '?example=nope&x=1')).toBe('/classes/new')
    expect(returnToWithExample('/classes/new', '')).toBe('/classes/new')
    expect(returnToWithExample('/classes/new', '?example=https://evil.example')).toBe('/classes/new')
  })
})

describe('the kept slug', () => {
  it('is recalled for 24 hours and then dropped and removed', () => {
    rememberExample('hill-challenge', 1000)
    expect(recallExample(1000 + EXAMPLE_TTL_MS - 1)).toBe('hill-challenge')
    expect(recallExample(1000 + EXAMPLE_TTL_MS)).toBeNull()
    expect(window.localStorage.getItem('classbeat.example')).toBeNull()
  })
  it('is gone after forgetExample', () => {
    rememberExample('hill-challenge')
    forgetExample()
    expect(recallExample()).toBeNull()
  })
  it('ignores a tampered entry', () => {
    for (const raw of ['not json', '{"slug":"nope","at":1}', '{"slug":"hill-challenge"}', '{"slug":"hill-challenge","at":"x"}', 'null']) {
      window.localStorage.setItem('classbeat.example', raw)
      expect(recallExample(5), raw).toBeNull()
    }
  })
  it('survives blocked storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(() => {
      rememberExample('hill-challenge')
      forgetExample()
    }).not.toThrow()
    expect(recallExample()).toBeNull()
  })
})
