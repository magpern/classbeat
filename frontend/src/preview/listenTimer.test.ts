import { describe, expect, it } from 'vitest'
import { LISTEN_LIMIT_MS, ListenTimer } from './listenTimer'

function make() {
  let time = 5000
  const timer = new ListenTimer(() => time)
  return { timer, advance: (ms: number) => { time += ms } }
}

describe('the listening counter: the sum of (stop - start) on a monotonic clock', () => {
  it('adds only the intervals between a start and a stop', () => {
    const { timer, advance } = make()
    advance(900) // nothing runs
    timer.start()
    advance(4000)
    timer.stop()
    advance(60_000) // paused
    timer.start()
    advance(1500)
    timer.stop()
    expect(timer.elapsedMs()).toBe(5500)
  })

  it('reads the running interval as well, without ending it', () => {
    const { timer, advance } = make()
    timer.start()
    advance(2500)
    expect(timer.elapsedMs()).toBe(2500)
    expect(timer.running).toBe(true)
    advance(500)
    expect(timer.elapsedMs()).toBe(3000)
  })

  it('does not count twice when started again while it runs, or stopped when it does not', () => {
    const { timer, advance } = make()
    timer.start()
    advance(1000)
    timer.start() // a second `playing` while running
    advance(1000)
    timer.stop()
    timer.stop()
    advance(5000)
    expect(timer.elapsedMs()).toBe(2000)
  })

  it('reaches the limit exactly at 15:00', () => {
    const { timer, advance } = make()
    expect(LISTEN_LIMIT_MS).toBe(900_000)
    timer.start()
    advance(899_999)
    expect(timer.reached()).toBe(false)
    advance(1)
    expect(timer.reached()).toBe(true)
  })

  it('starts a new session at 0', () => {
    const { timer, advance } = make()
    timer.start()
    advance(10_000)
    timer.reset()
    expect(timer.elapsedMs()).toBe(0)
    expect(timer.running).toBe(false)
  })
})
