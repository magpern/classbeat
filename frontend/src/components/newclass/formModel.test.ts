import { describe, expect, it } from 'vitest'
import { t } from '../../i18n/t'
import { leftOut, validateForm, whole, type FormValues } from './formModel'

const BASE: FormValues = {
  title: 'Hills',
  playlist: 'p1',
  mixId: '',
  music: 'playlist',
  running: false,
  noMusic: false,
  length: '45',
  intensity: 'moderate',
  climbs: '3',
  sprints: '2',
  races: '1',
  ftp: '',
  style: '',
  notes: '',
  racesOn: true,
  ftpOn: true,
  maxMinutes: null,
}

const tt = ((key: string) => key) as unknown as Parameters<typeof validateForm>[0]

function problem(v: Partial<FormValues>): string | null {
  const r = validateForm(tt, { ...BASE, ...v })
  return 'problem' in r ? r.problem : null
}

describe('whole', () => {
  it('is NaN for an empty field and the number otherwise', () => {
    expect(whole('  ')).toBeNaN()
    expect(whole('7')).toBe(7)
  })
})

describe('validateForm', () => {
  it('builds the cycling brief with the FTP test as a number', () => {
    const r = validateForm(tt, { ...BASE, ftp: '20', style: 'Pop', notes: 'hi' })
    expect(r).toEqual({
      brief: { length_min: 45, climbs: 3, sprints: 2, races: 1, intensity: 'moderate', ftp_test: 20, style: 'Pop', text: 'hi', explicit: ['length_min', 'climbs', 'sprints', 'races', 'ftp_test'] },
    })
  })

  it('builds a running brief with none of the bike fields', () => {
    const r = validateForm(tt, { ...BASE, running: true, climbs: '', ftp: '5' })
    expect(r).toEqual({ brief: { sport: 'running', length_min: 45, intensity: 'moderate', style: '', text: '', explicit: ['length_min'] } })
  })

  it('states only the values the form really offers as explicit', () => {
    const paths = (v: Partial<FormValues>) => {
      const r = validateForm(tt, { ...BASE, ...v })
      return 'brief' in r ? r.brief.explicit : null
    }
    expect(paths({ ftp: '' })).toEqual(['length_min', 'climbs', 'sprints', 'races'])
    expect(paths({ racesOn: false, ftpOn: false, ftp: '3' })).toEqual(['length_min', 'climbs', 'sprints'])
    expect(paths({ climbs: '0', sprints: '0', races: '0' })).toEqual(['length_min'])
    expect(paths({ running: true })).toEqual(['length_min'])
  })

  it('zeroes races and the FTP test when the plan leaves them out', () => {
    const r = validateForm(tt, { ...BASE, racesOn: false, ftpOn: false, races: 'x', ftp: '3' })
    expect(r).toMatchObject({ brief: { races: 0, ftp_test: null } })
  })

  it.each([
    [{ title: ' ' }, 'newclass.invalid.name'],
    [{ title: 'x'.repeat(101) }, 'newclass.invalid.name'],
    [{ playlist: '' }, 'newclass.invalid.playlist'],
    [{ music: 'swedebeat_mix' as const, mixId: '' }, 'newclass.invalid.mix'],
    [{ length: '9' }, 'newclass.invalid.length'],
    [{ length: '181' }, 'newclass.invalid.length'],
    [{ climbs: '11' }, 'newclass.invalid.climbs'],
    [{ sprints: '21' }, 'newclass.invalid.sprints'],
    [{ races: '6' }, 'newclass.invalid.races'],
    [{ style: 'x'.repeat(61) }, 'newclass.invalid.style'],
    [{ notes: 'x'.repeat(2001) }, 'newclass.invalid.notes'],
    [{ notes: 'å'.repeat(2000) }, 'newclass.invalid.briefTooLong'],
  ])('refuses %j with %s', (values, key) => {
    expect(problem(values)).toBe(key)
  })

  it('needs no playlist for No music, and none for a mix choice while running', () => {
    expect(problem({ noMusic: true, playlist: '' })).toBeNull()
    expect(problem({ running: true, music: 'swedebeat_mix', playlist: 'p1' })).toBeNull()
    expect(problem({ running: true, playlist: '' })).toBe('newclass.invalid.playlist')
  })

  it('refuses a length over the plan cap with the cap sentence', () => {
    expect(problem({ length: '60', maxMinutes: 45 })).toBe('newclass.planMaxLength')
    expect(problem({ length: '45', maxMinutes: 45 })).toBeNull()
  })
})

describe('leftOut', () => {
  it('names nothing, one part or a list', () => {
    expect(leftOut(t, [])).toBeNull()
    expect(leftOut(t, ['races'])).toBe('Your plan does not include races.')
    expect(leftOut(t, ['races', 'FTP tests'])).toBe('Your plan does not include races and FTP tests.')
  })
})
