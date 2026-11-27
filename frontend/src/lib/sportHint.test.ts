import { describe, expect, it } from 'vitest'
import { AUTO_CYCLING, chooseSport, followText, initialToggle, sportHint, sportOverride, words, type Sport } from './sportHint'

// #1544: the start screen's toggle follows the request (en, sv, da) until the instructor chooses by hand.

describe('the sport a request names', () => {
  it.each<[string, Sport]>([
    // en: the running words, whole words, any case
    ['A 45 min run with intervals', 'running'],
    ['Running intervals, 30 minutes', 'running'],
    ['RUN hard for 20 minutes', 'running'],
    ['an easy jog to warm up', 'running'],
    ['Jogging and strides', 'running'],
    ['a treadmill session', 'running'],
    ['runs of 4 minutes, run!', 'running'],
    // sv
    ['En löpning på 40 minuter', 'running'],
    ['Vi ska springa backintervaller', 'running'],
    ['Ett löppass med fartlek', 'running'],
    ['jogga lugnt i 30 min', 'running'],
    // da
    ['En løbetur på 45 minutter', 'running'],
    ['Løb med intervaller', 'running'],
    ['et løbehold i aften', 'running'],
    ['vi skal løbe 5 km', 'running'],
    // word boundaries: a running word inside another word never counts
    ['for runners who also ride', 'cycling'],
    ['three climbs and a rerun of last week', 'cycling'],
    ['Bruntime hills', 'cycling'],
    ['spring into the climbs', 'cycling'],
    ['en springare', 'cycling'],
    ['løbende intervaller på cyklen', 'cycling'],
    ['a trunk of hills', 'cycling'],
    // both sports, or a ride: cycling (the reading refuses both politely)
    ['A ride, then a 20 min run', 'cycling'],
    ['Cycling class for triathletes who run', 'cycling'],
    ['spin class with a run-style finish', 'cycling'],
    ['Ett cykelpass och sedan löpning', 'cycling'],
    ['et cykelhold, så en løbetur', 'cycling'],
    // neither
    ['45 minutes, three climbs', 'cycling'],
    ['', 'cycling'],
  ])('%j is %s', (text, sport) => {
    expect(sportHint(text)).toBe(sport)
  })

  it('splits on anything that is not a letter or a digit, in any script', () => {
    expect(words('Löb, løb; RUN-run 2x')).toEqual(['löb', 'løb', 'run', 'run', '2x'])
  })
})

describe('the toggle while the instructor types', () => {
  it('flips to Running when the words name a run, and back when they are gone', () => {
    const typed = ['A 45', 'A 45 min run', 'A 45 min run with intervals', 'A 45 min ru', 'A 45 min ride']
    const states = typed.reduce<ReturnType<typeof followText>[]>((all, text) => [...all, followText(all.at(-1) ?? AUTO_CYCLING, text)], [])
    expect(states.map((s) => s.sport)).toEqual(['cycling', 'running', 'running', 'cycling', 'cycling'])
    expect(states.every((s) => !s.manual)).toBe(true)
  })

  it('never moves a sport chosen by hand, either way', () => {
    const byHand = chooseSport('cycling')
    expect(followText(byHand, 'A 45 min run')).toBe(byHand)
    const running = chooseSport('running')
    expect(followText(running, 'A spin class')).toBe(running)
    expect(followText(followText(running, ''), 'løbetur')).toEqual({ sport: 'running', manual: true })
  })

  it('keeps the same state object when nothing changes', () => {
    const state = followText(AUTO_CYCLING, 'a run')
    expect(followText(state, 'a run with strides')).toBe(state)
  })
})

describe('what the request sends', () => {
  it('sends a choice by hand as it is, Running always, and nothing for a Cycling the words chose', () => {
    expect(sportOverride(chooseSport('cycling'))).toBe('cycling')
    expect(sportOverride(chooseSport('running'))).toBe('running')
    expect(sportOverride({ sport: 'running', manual: false })).toBe('running')
    expect(sportOverride(AUTO_CYCLING)).toBeUndefined()
  })
})

describe('the first state', () => {
  it('follows the words, and keeps a kept sport by hand only when the words say otherwise', () => {
    expect(initialToggle('A 30 min run')).toEqual({ sport: 'running', manual: false })
    expect(initialToggle('three climbs')).toEqual(AUTO_CYCLING)
    expect(initialToggle('three climbs', 'running')).toEqual({ sport: 'running', manual: true })
    expect(initialToggle('A 30 min run', 'cycling')).toEqual({ sport: 'cycling', manual: true })
    expect(initialToggle('A 30 min run', 'running')).toEqual({ sport: 'running', manual: false })
  })
})
